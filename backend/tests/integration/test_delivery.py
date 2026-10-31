import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import Settings
from app.integrations.jira.client import JiraClient
from app.integrations.jira.discovery import discover_project
from app.integrations.jira.errors import JiraProjectNotAllowedError
from app.integrations.jira.execution import JiraExecutor
from app.orchestration.policy import Disposition, Mode
from app.orchestration.runner import JobRunner
from app.persistence.models import (
    ActionIntent,
    Client,
    ConflictRecord,
    Evidence,
    JiraIssueMapping,
    JiraSchemaSnapshot,
    Meeting,
    PolicyDecision,
    Requirement,
    Stakeholder,
)
from app.providers.llm.fake import FakeLLMProvider
from app.services import delivery, flags
from tests.support.fake_jira import FakeJira

GOOD_DRAFT = {
    "issue_type": "Story",
    "summary": "Reset password by email",
    "description": "Customers can reset their password through an emailed link.",
    "acceptance_criteria": [
        "Reset link expires 15 minutes after it is sent",
        "Reset email is sent within 60 seconds of the request",
    ],
    "labels": ["auth"],
}
VAGUE_DRAFT = {**GOOD_DRAFT, "acceptance_criteria": ["The reset should be fast", "Easy to use"]}


async def no_sleep(_):
    return None


def settings(mode="dry_run", **kw):
    return Settings(autonomy_mode=mode, jira_project_allowlist="SCRUM", kill_switch=False, **kw)


def executor_for(fake: FakeJira) -> JiraExecutor:
    client = JiraClient("https://x.atlassian.net", "a@b.c", "tok", transport=fake.transport())
    return JiraExecutor(client, {"SCRUM"}, sleep=no_sleep, poll_attempts=3)


async def seed_schema(factory, client_id, fake):
    ex = executor_for(fake)
    snap = await discover_project(ex.client, "SCRUM", {"SCRUM"})
    with factory() as s:
        s.add(JiraSchemaSnapshot(client_id=client_id, project_key="SCRUM", payload=snap))
        s.commit()
    return snap


def seed_requirement(factory, client_id, status="confirmed", title="Password reset by email"):
    with factory() as s:
        m = Meeting(client_id=client_id, title="Kickoff", source="transcript_file")
        s.add(m)
        s.flush()
        sh = Stakeholder(
            client_id=client_id, name="Priya", has_decision_authority=True, identity_verified=True
        )
        s.add(sh)
        s.flush()
        r = Requirement(
            client_id=client_id,
            meeting_id=m.id,
            title=title,
            statement="Customers reset passwords by email; link expires in 15 minutes",
            kind="functional",
            explicit=True,
            status=status,
        )
        s.add(r)
        s.flush()
        s.add(
            Evidence(
                client_id=client_id,
                meeting_id=m.id,
                requirement_id=r.id,
                segment_index=0,
                quote="reset their password by email",
                speaker_label="Priya",
                stakeholder_id=sh.id,
                start_s=65.0,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        s.commit()
        return r.id


async def run_jobs(factory, client_id, fake, st):
    handler = delivery.make_execute_handler(st, lambda: executor_for(fake))
    runner = JobRunner(factory, {delivery.EXECUTE_JOB: handler}, client_id=client_id)
    return await runner.run_until_empty()


async def pipeline(factory, client_id, fake, mode, draft=GOOD_DRAFT, **req_kw):
    st = settings(mode)
    await seed_schema(factory, client_id, fake)
    rid = seed_requirement(factory, client_id, **req_kw)
    llm = FakeLLMProvider(lambda r: draft)
    with factory() as s:
        await delivery.draft_story(s, llm, client_id, rid, "SCRUM")
        ready = await delivery.make_ready_if_measurable(s, client_id, rid)
        s.commit()
    return st, rid, ready


async def prepare(factory, client_id, rid, st, fake):
    ex = executor_for(fake)
    with factory() as s:
        prepared = await delivery.prepare_delivery(s, st, ex, client_id, rid, "SCRUM", "tester")
        s.commit()
        return (
            prepared.disposition,
            prepared.intent.id if prepared.intent else None,
            prepared.reasons,
        )


async def test_discovery_requires_allowlist_and_captures_hierarchy():
    fake = FakeJira()
    ex = executor_for(fake)
    with pytest.raises(JiraProjectNotAllowedError):
        await discover_project(ex.client, "OTHER", {"SCRUM"})
    snap = await discover_project(ex.client, "SCRUM", {"SCRUM"})
    names = {t["name"]: t for t in snap["issue_types"]}
    assert names["Epic"]["hierarchy_level"] == 1 and names["Subtask"]["subtask"] is True
    assert "summary" in names["Story"]["required_fields"]


async def test_dry_run_persists_intent_and_makes_zero_jira_writes(factory, client_id):
    fake = FakeJira()
    st, rid, ready = await pipeline(factory, client_id, fake, "dry_run")
    assert ready
    fake.calls.clear()
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.DRY_RUN
    assert fake.write_calls == [] and not fake.issues
    with factory() as s:
        intent = s.get(ActionIntent, iid)
        assert intent.status == "dry_run" and intent.payload["summary"] == "Reset password by email"
        assert intent.marker.startswith("ba-intent-")
    assert await run_jobs(factory, client_id, fake, st) == 0  # nothing queued


async def test_propose_waits_for_approval_then_executes_exactly_once(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "propose")
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.AWAITING_APPROVAL and not fake.issues
    assert await run_jobs(factory, client_id, fake, st) == 0
    with factory() as s:
        delivery.approve_intent(s, client_id, iid, "reviewer@example.com")
        s.commit()
    assert await run_jobs(factory, client_id, fake, st) == 1
    assert len(fake.issues) == 1
    with factory() as s:
        intent = s.get(ActionIntent, iid)
        assert intent.status == "succeeded" and intent.approved_by == "reviewer@example.com"
        assert s.scalars(
            select(JiraIssueMapping).where(JiraIssueMapping.client_id == client_id)
        ).one()
    # approving again is refused: no double execution
    with factory() as s, pytest.raises(delivery.DeliveryError):
        delivery.approve_intent(s, client_id, iid, "x")


async def test_autonomous_creates_with_marker_provenance_and_evidence_comment(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "autonomous")
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.EXECUTE_NOW
    await run_jobs(factory, client_id, fake, st)
    (issue,) = fake.issues.values()
    labels = issue["fields"]["labels"]
    assert any(label.startswith("ba-intent-") for label in labels) and "ba-generated" in labels
    comment = str(fake.comments[issue["key"]])
    assert (
        "reset their password by email" in comment and "identity unverified" not in comment
    )  # verified
    assert "/requirements/" in comment
    # re-preparing a synced requirement never creates a second issue
    disp2, _, reasons = await prepare(factory, client_id, rid, st, fake)
    assert disp2 is Disposition.CLARIFY and len(fake.issues) == 1


async def test_policy_decision_is_persisted_with_rules_and_reason(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "propose")
    await prepare(factory, client_id, rid, st, fake)
    with factory() as s:
        d = s.scalars(select(PolicyDecision).where(PolicyDecision.client_id == client_id)).one()
        assert d.outcome == "execute" and d.rule_ids == ["R-PASS"] and d.reason
        assert d.inputs["mode"] == "propose" and d.inputs["kind"] == "create_issue"


async def test_vague_acceptance_criteria_block_readiness_and_delivery(factory, client_id):
    fake = FakeJira()
    st, rid, ready = await pipeline(factory, client_id, fake, "autonomous", draft=VAGUE_DRAFT)
    assert ready is False
    with factory() as s:
        r = s.get(Requirement, rid)
        assert r.status == "confirmed"
        d = delivery.latest_draft(s, r)
        assert not d.ac_measurable and any("vague" in f for f in d.review_flags)
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.CLARIFY and iid is None and not fake.issues


async def test_invalid_issue_type_from_llm_is_rejected_before_any_network(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(
        factory, client_id, fake, "autonomous", draft={**GOOD_DRAFT, "issue_type": "Bug"}
    )
    fake.calls.clear()
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp in {Disposition.REJECTED, Disposition.CLARIFY} and iid is None
    assert fake.write_calls == []


async def test_exact_duplicate_becomes_reference_comment_not_new_issue(factory, client_id):
    fake = FakeJira()
    fake._new_issue({"summary": "Reset password by email", "labels": []})
    st, rid, _ = await pipeline(factory, client_id, fake, "autonomous")
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.EXECUTE_NOW
    await run_jobs(factory, client_id, fake, st)
    assert len(fake.issues) == 1  # no new issue
    with factory() as s:
        intent = s.get(ActionIntent, iid)
        assert intent.kind == "add_reference" and intent.status == "succeeded"
        assert (
            s.scalars(select(JiraIssueMapping).where(JiraIssueMapping.client_id == client_id))
            .one()
            .relation
            == "reference"
        )


async def test_fuzzy_duplicates_escalate_and_hold(factory, client_id):
    fake = FakeJira()
    fake._new_issue({"summary": "Reset password by email flow v2", "labels": []})
    fake._new_issue({"summary": "Password reset by email legacy", "labels": []})
    st, rid, _ = await pipeline(factory, client_id, fake, "autonomous")
    disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.HELD
    with factory() as s:
        assert s.get(ActionIntent, iid).status == "held"
    assert await run_jobs(factory, client_id, fake, st) == 0
    assert len(fake.issues) == 2


async def test_kill_switch_holds_even_in_autonomous_mode(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "autonomous")
    with factory() as s:
        flags.set_kill_switch(s, True, "tester")
        s.commit()
    try:
        disp, iid, _ = await prepare(factory, client_id, rid, st, fake)
        assert disp is Disposition.HELD and len(fake.issues) == 0
    finally:
        with factory() as s:
            flags.set_kill_switch(s, False, "tester")
            s.commit()


async def test_kill_switch_flipped_after_approval_still_blocks_execution(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "propose")
    _, iid, _ = await prepare(factory, client_id, rid, st, fake)
    with factory() as s:
        delivery.approve_intent(s, client_id, iid, "r")
        flags.set_kill_switch(s, True, "tester")
        s.commit()
    try:
        await run_jobs(factory, client_id, fake, st)
        assert not fake.issues
        with factory() as s:
            assert s.get(ActionIntent, iid).status == "held"
    finally:
        with factory() as s:
            flags.set_kill_switch(s, False, "tester")
            s.commit()


async def test_open_conflict_escalates(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "autonomous")
    with factory() as s:
        other = Requirement(
            client_id=client_id,
            title="o",
            statement="o",
            kind="rule",
            explicit=True,
            status="confirmed",
        )
        s.add(other)
        s.flush()
        s.add(
            ConflictRecord(
                client_id=client_id,
                requirement_id=rid,
                other_requirement_id=other.id,
                relation="contradiction",
                similarity=0.9,
                explanation="x",
            )
        )
        s.commit()
    disp, _, _ = await prepare(factory, client_id, rid, st, fake)
    assert disp is Disposition.HELD and not fake.issues


async def test_project_autonomy_cannot_exceed_global(factory, client_id):
    from app.persistence.models import Project

    with factory() as s:
        p = Project(client_id=client_id, name="p", autonomy_mode="autonomous")
        s.add(p)
        s.flush()
        assert flags.autonomy_mode(s, settings("propose"), p) is Mode.PROPOSE
        p.autonomy_mode = "dry_run"
        assert flags.autonomy_mode(s, settings("autonomous"), p) is Mode.DRY_RUN


async def test_timeout_after_create_survives_full_pipeline_without_duplicates(factory, client_id):
    fake = FakeJira()
    fake.create_faults = ["timeout_after_apply"]
    st, rid, _ = await pipeline(factory, client_id, fake, "autonomous")
    await prepare(factory, client_id, rid, st, fake)
    await run_jobs(factory, client_id, fake, st)
    assert len(fake.issues) == 1
    with factory() as s:
        assert (
            s.scalars(select(ActionIntent).where(ActionIntent.client_id == client_id)).one().status
            == "succeeded"
        )


async def test_other_client_cannot_approve_intent(factory, client_id):
    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "propose")
    _, iid, _ = await prepare(factory, client_id, rid, st, fake)
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        with pytest.raises(LookupError):
            delivery.approve_intent(s, other.id, iid, "attacker")


async def test_requirement_superseded_mid_workflow_cancels_pending_action(factory, client_id):
    from app.domain.requirement import ReqStatus
    from app.services import knowledge

    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "propose")
    _, iid, _ = await prepare(factory, client_id, rid, st, fake)
    with factory() as s:
        delivery.approve_intent(s, client_id, iid, "reviewer")
        s.commit()
    with factory() as s:  # the requirement is superseded after approval but before execution
        knowledge.transition_requirement(
            s, client_id, rid, ReqStatus.SUPERSEDED, "replaced by newer"
        )
        s.commit()
    await run_jobs(factory, client_id, fake, st)
    assert not fake.issues
    with factory() as s:
        i = s.get(ActionIntent, iid)
        assert i.status == "cancelled" and "superseded" in i.last_error


async def test_requirement_revised_after_proposal_is_not_executed_stale(factory, client_id):
    from app.services import knowledge

    fake = FakeJira()
    st, rid, _ = await pipeline(factory, client_id, fake, "propose")
    _, iid, _ = await prepare(factory, client_id, rid, st, fake)
    with factory() as s:
        delivery.approve_intent(s, client_id, iid, "reviewer")
        knowledge.revise_requirement(
            s, client_id, rid, "Reset (10 min)", "now 10 minutes", "changed"
        )
        s.commit()
    await run_jobs(factory, client_id, fake, st)
    assert not fake.issues
    with factory() as s:
        assert "revised after this proposal" in s.get(ActionIntent, iid).last_error
