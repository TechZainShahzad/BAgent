"""Requirement -> draft -> review -> policy -> ActionIntent -> (approval) -> Jira Execution.

Mutation flow (CLAUDE.md section 9): search existing -> build change -> policy check ->
schema/permission check -> persist ActionIntent -> execute -> reconcile -> mapping -> audit.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.agents import story_architect
from app.agents.testability import review_acceptance_criteria
from app.config import Settings
from app.domain.grounding import normalize
from app.domain.jira_schema import SchemaView
from app.domain.requirement import ReqStatus
from app.integrations.jira import adf
from app.integrations.jira.execution import JiraExecutor, build_executor, marker_for
from app.orchestration import jobs
from app.orchestration.audit import append_audit
from app.orchestration.jobs import Handler
from app.orchestration.policy import (
    Disposition,
    Op,
    PolicyInput,
    PolicyResult,
    disposition,
    evaluate,
)
from app.persistence.models import (
    ActionIntent,
    ConflictRecord,
    Evidence,
    JiraIssueMapping,
    JiraSchemaSnapshot,
    Job,
    Meeting,
    OpenQuestion,
    PolicyDecision,
    Project,
    Requirement,
    Stakeholder,
    StoryDraft,
)
from app.providers.llm.base import LLMProvider
from app.services import flags
from app.services.jira_projects import effective_allowlist
from app.services.knowledge import transition_requirement

EXECUTE_JOB = "jira.execute_intent"


class DeliveryError(Exception):
    """A delivery precondition failed (surfaced to the caller, never swallowed)."""


class SearchPort(Protocol):
    async def search_existing(self, project_key: str, summary: str) -> list[dict[str, Any]]: ...


@dataclass
class Prepared:
    intent: ActionIntent | None
    draft: StoryDraft
    decision: PolicyDecision
    disposition: Disposition
    reasons: list[str]


def latest_schema(session: Session, client_id: uuid.UUID, project_key: str) -> SchemaView:
    snap = session.scalars(
        select(JiraSchemaSnapshot)
        .where(
            JiraSchemaSnapshot.client_id == client_id, JiraSchemaSnapshot.project_key == project_key
        )
        .order_by(JiraSchemaSnapshot.fetched_at.desc())
        .limit(1)
    ).first()
    if snap is None:
        raise DeliveryError(f"no Jira schema snapshot for {project_key}; run jira-discover first")
    return SchemaView(snap.payload)


def _requirement(session: Session, client_id: uuid.UUID, requirement_id: uuid.UUID) -> Requirement:
    req = session.get(Requirement, requirement_id)
    if req is None or req.client_id != client_id:
        raise LookupError("requirement not found in client scope")
    return req


def _evidence(session: Session, req: Requirement) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence).where(
                Evidence.requirement_id == req.id,
                Evidence.client_id == req.client_id,
                Evidence.grounded.is_(True),
            )
        )
    )


def latest_draft(session: Session, req: Requirement) -> StoryDraft | None:
    return session.scalars(
        select(StoryDraft)
        .where(
            StoryDraft.requirement_id == req.id,
            StoryDraft.client_id == req.client_id,
            StoryDraft.requirement_version == req.current_version,
        )
        .order_by(StoryDraft.created_at.desc())
        .limit(1)
    ).first()


def _known_context(session: Session, req: Requirement) -> list[str]:
    """Other requirements captured from the same meeting, so answered points are not re-asked."""
    if req.meeting_id is None:
        return []
    others = session.scalars(
        select(Requirement)
        .where(
            Requirement.client_id == req.client_id,
            Requirement.meeting_id == req.meeting_id,
            Requirement.id != req.id,
            Requirement.status.notin_(("rejected", "cancelled", "superseded")),
        )
        .order_by(Requirement.created_at)
        .limit(30)
    )
    return [f"{o.title}: {o.statement}" for o in others]


# ----------------------------------------------------------------------------- drafting
async def draft_story(
    session: Session,
    provider: LLMProvider,
    client_id: uuid.UUID,
    requirement_id: uuid.UUID,
    project_key: str,
    parent_key: str | None = None,
) -> StoryDraft:
    req = _requirement(session, client_id, requirement_id)
    schema = latest_schema(session, client_id, project_key)
    evidence = _evidence(session, req)
    if not evidence:
        raise DeliveryError("requirement has no grounded evidence")
    creatable = [t.name for t in schema.types.values() if not t.subtask]
    out = await story_architect.draft(
        provider,
        str(client_id),
        req.title,
        req.statement,
        req.kind,
        [story_architect.EvidenceIn(e.quote, e.speaker_label) for e in evidence],
        creatable,
        known_context=_known_context(session, req),
    )
    issue_type = out.issue_type
    epic_t = schema.epic_type()
    if req.kind == "goal" and epic_t is not None:
        issue_type = epic_t.name  # deterministic: a goal is the Epic, whatever the model chose
    chosen = schema.type(issue_type)
    is_epic = bool(chosen and chosen.hierarchy_level == 1)
    review = review_acceptance_criteria(out.acceptance_criteria)
    if is_epic:  # an epic states scope; testable criteria belong to its stories
        review = type(review)(True, [])
    problems = schema.validate_draft(
        out.issue_type, out.summary, parent_key, {"labels", "description"}
    )
    if req.kind != "goal" and (schema.type(out.issue_type) or None) is not None:
        t = schema.type(out.issue_type)
        if t is not None and t.hierarchy_level == 1:
            problems.append("epics are only drafted for 'goal' requirements")
    flags_ = [*review.flags, *[f"schema: {p}" for p in problems]]
    draft = StoryDraft(
        client_id=client_id,
        requirement_id=req.id,
        requirement_version=req.current_version,
        project_key=project_key,
        issue_type=issue_type,
        summary=out.summary,
        description=out.description,
        acceptance_criteria=out.acceptance_criteria,
        parent_key=parent_key,
        review_flags=flags_,
        ac_measurable=review.measurable and not problems,
        technical_notes=out.technical_notes,
        assumptions=out.assumptions_to_confirm,
    )
    session.add(draft)
    session.flush()
    append_audit(
        session,
        "draft.created",
        {
            "draft_id": str(draft.id),
            "requirement_id": str(req.id),
            "measurable": draft.ac_measurable,
            "flags": len(flags_),
        },
        client_id,
    )
    return draft


# ----------------------------------------------------------------------------- builders
def description_adf(
    req: Requirement,
    draft: StoryDraft,
    meeting_title: str | None,
    extra_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [adf.paragraph(draft.description)]
    if draft.acceptance_criteria:
        blocks += [adf.heading("Acceptance criteria"), adf.bullet_list(draft.acceptance_criteria)]
    blocks += extra_blocks or []
    if draft.technical_notes:
        blocks += [
            adf.heading("Technical considerations (assistant-suggested, not stated by the client)"),
            adf.bullet_list(draft.technical_notes),
        ]
    if draft.assumptions:
        blocks += [adf.heading("To confirm with the client"), adf.bullet_list(draft.assumptions)]
    src = f"Source: BA platform requirement {req.id} (v{req.current_version})"
    if meeting_title:
        src += f", meeting: {meeting_title}"
    blocks += [adf.heading("Provenance"), adf.paragraph(src)]
    return adf.doc(*blocks)


def evidence_comment(
    session: Session, req: Requirement, marker: str, settings: Settings, meeting_title: str | None
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        adf.paragraph(f"Evidence recorded by the BA platform ({marker}:evidence)", bold=True)
    ]
    for ev in _evidence(session, req):
        who = ev.speaker_label or "unknown speaker"
        verified = False
        if ev.stakeholder_id:
            sh = session.get(Stakeholder, ev.stakeholder_id)
            verified = bool(sh and sh.identity_verified)
        when = (
            f" at {int(ev.start_s // 60):02d}:{int(ev.start_s % 60):02d}"
            if ev.start_s is not None
            else ""
        )
        attribution = (
            "verified identity"
            if verified
            else "speaker label as written in source, identity unverified"
        )
        blocks.append(adf.paragraph(f'"{ev.quote}" - {who}{when} ({attribution})'))
    if meeting_title:
        blocks.append(adf.paragraph(f"Meeting: {meeting_title}"))
    blocks.append(adf.paragraph(f"Dashboard: {settings.dashboard_base_url}/requirements/{req.id}"))
    return adf.doc(*blocks)


# ----------------------------------------------------------------------------- policy + intent
def _counts(session: Session, req: Requirement) -> tuple[int, int]:
    blocking = (
        session.scalar(
            select(func.count())
            .select_from(OpenQuestion)
            .where(
                OpenQuestion.client_id == req.client_id,
                OpenQuestion.blocking.is_(True),
                OpenQuestion.blocks_requirement_ids.contains([str(req.id)]),
                OpenQuestion.status.in_(("open", "asked")),
            )
        )
        or 0
    )
    conflicts = (
        session.scalar(
            select(func.count())
            .select_from(ConflictRecord)
            .where(
                ConflictRecord.client_id == req.client_id,
                ConflictRecord.status == "open",
                ConflictRecord.relation == "contradiction",
                (ConflictRecord.requirement_id == req.id)
                | (ConflictRecord.other_requirement_id == req.id),
            )
        )
        or 0
    )
    return int(blocking), int(conflicts)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", normalize(s).casefold())


async def prepare_delivery(
    session: Session,
    settings: Settings,
    executor: JiraExecutor | None,
    client_id: uuid.UUID,
    requirement_id: uuid.UUID,
    project_key: str,
    requested_by: str,
    op: Op | None = None,
    extra_blocks: list[dict[str, Any]] | None = None,
) -> Prepared:
    """Evaluate a drafted requirement and persist a PolicyDecision plus (usually) an ActionIntent."""
    req = _requirement(session, client_id, requirement_id)
    draft = latest_draft(session, req)
    if draft is None:
        raise DeliveryError("no draft for the current requirement version; draft it first")
    schema = latest_schema(session, client_id, project_key)
    project = session.get(Project, req.project_id) if req.project_id else None
    mode = flags.autonomy_mode(session, settings, project)
    meeting = session.get(Meeting, req.meeting_id) if req.meeting_id else None
    meeting_title = meeting.title if meeting else None

    mapped = session.scalars(
        select(JiraIssueMapping).where(
            JiraIssueMapping.client_id == client_id,
            JiraIssueMapping.requirement_id == req.id,
            JiraIssueMapping.relation == "primary",
        )
    ).first()

    # 1. search existing (read-only)
    kind = "create_issue"
    target_key: str | None = None
    dup_count = 0
    unambiguous = True
    if mapped is not None:
        kind, target_key = "update_issue", mapped.jira_key
    elif executor is not None:
        from app.integrations.jira.execution import search_duplicates

        found = await search_duplicates(executor.client, project_key, draft.summary)
        exact = [i for i in found if _norm(i["fields"].get("summary", "")) == _norm(draft.summary)]
        if len(exact) == 1 and len(found) == 1:
            kind, target_key = "add_reference", exact[0]["key"]
        else:
            dup_count = len(found)
            unambiguous = dup_count == 0

    # 2. policy
    schema_type = schema.type(draft.issue_type)
    is_epic = bool(schema_type and schema_type.hierarchy_level == 1)
    if op is None:
        op = {
            "update_issue": Op.UPDATE_TEXT,
            "add_reference": Op.ADD_REFERENCE,
        }.get(kind, Op.CREATE_EPIC if is_epic else Op.CREATE_STORY_TASK)
    blocking, conflicts = _counts(session, req)
    problems = [f for f in draft.review_flags if f.startswith("schema:")]
    pin = PolicyInput(
        op=op,
        kill_switch=flags.kill_switch_on(session, settings),
        project_allowlisted=project_key in effective_allowlist(session, settings),
        requirement_status=req.status,
        evidence_present=bool(_evidence(session, req)),
        ac_measurable=draft.ac_measurable,
        open_blocking_questions=blocking,
        open_conflicts=conflicts,
        duplicate_candidates=dup_count,
        draft_valid=not problems,
        hierarchy_ok=is_epic and schema.epic_type() is not None,
        change_clearly_evidenced=kind == "update_issue"
        and bool(mapped and mapped.last_synced_version < req.current_version),
        target_unambiguous=unambiguous and (target_key is not None or kind == "create_issue"),
    )
    result: PolicyResult = evaluate(pin)
    disp = disposition(result, mode)
    decision = PolicyDecision(
        client_id=client_id,
        project_id=req.project_id,
        subject_type="requirement",
        subject_id=str(req.id),
        outcome=result.outcome.value,
        rule_ids=list(result.rule_ids),
        inputs={
            **{k: (v.value if hasattr(v, "value") else v) for k, v in pin.__dict__.items()},
            "mode": mode.value,
            "disposition": disp.value,
            "kind": kind,
        },
        reason=result.reason,
    )
    session.add(decision)
    session.flush()
    append_audit(
        session,
        "policy.decision",
        {
            "decision_id": str(decision.id),
            "outcome": result.outcome.value,
            "rules": list(result.rule_ids),
            "disposition": disp.value,
            "mode": mode.value,
        },
        client_id,
    )
    reasons = [result.reason]
    if disp in {Disposition.REJECTED, Disposition.CLARIFY}:
        return Prepared(None, draft, decision, disp, reasons)

    # 3. persist the ActionIntent (before any side effect)
    iid = uuid.uuid4()
    marker = marker_for(iid)
    payload: dict[str, Any] = {"requested_by": requested_by, "mode": mode.value}
    if kind == "create_issue":
        assert schema_type is not None
        payload.update(
            issue_type_id=schema_type.id,
            issue_type=schema_type.name,
            summary=draft.summary,
            description_adf=description_adf(req, draft, meeting_title, extra_blocks),
            labels=["ba-generated"],
            parent_key=draft.parent_key,
            evidence_comment_adf=evidence_comment(session, req, marker, settings, meeting_title),
        )
    elif kind == "update_issue":
        payload.update(
            jira_key=target_key,
            summary=draft.summary,
            description_adf=description_adf(req, draft, meeting_title, extra_blocks),
            evidence_comment_adf=evidence_comment(session, req, marker, settings, meeting_title),
        )
    else:
        payload.update(
            jira_key=target_key,
            comment_adf=adf.doc(
                adf.paragraph(
                    f"BA platform: related requirement {req.id} was discussed ({marker})", bold=True
                ),
                adf.paragraph(f"Meeting: {meeting_title or 'n/a'}. Requirement: {req.title}"),
                adf.paragraph(f"Dashboard: {settings.dashboard_base_url}/requirements/{req.id}"),
            ),
        )
    status = {
        Disposition.DRY_RUN: "dry_run",
        Disposition.AWAITING_APPROVAL: "awaiting_approval",
        Disposition.EXECUTE_NOW: "approved",
        Disposition.HELD: "held",
    }[disp]
    intent = ActionIntent(
        id=iid,
        client_id=client_id,
        project_id=req.project_id,
        requirement_id=req.id,
        draft_id=draft.id,
        kind=kind,
        status=status,
        project_key=project_key,
        payload=payload,
        marker=marker,
        policy_decision_id=decision.id,
    )
    session.add(intent)
    session.flush()
    append_audit(
        session,
        "intent.created",
        {"intent_id": str(iid), "kind": kind, "status": status, "requirement_id": str(req.id)},
        client_id,
    )
    if disp is Disposition.EXECUTE_NOW:
        jobs.enqueue(session, client_id, EXECUTE_JOB, str(iid), {"intent_id": str(iid)})
    return Prepared(intent, draft, decision, disp, reasons)


async def make_ready_if_measurable(
    session: Session, client_id: uuid.UUID, requirement_id: uuid.UUID
) -> bool:
    """Testability gate: move confirmed -> ready_for_delivery only with measurable AC."""
    req = _requirement(session, client_id, requirement_id)
    draft = latest_draft(session, req)
    if req.status == ReqStatus.READY_FOR_DELIVERY.value:
        return True
    if req.status != ReqStatus.CONFIRMED.value or draft is None or not draft.ac_measurable:
        return False
    transition_requirement(
        session,
        client_id,
        requirement_id,
        ReqStatus.READY_FOR_DELIVERY,
        "testability review passed",
    )
    return True


# ----------------------------------------------------------------------------- approvals
def approve_intent(
    session: Session, client_id: uuid.UUID, intent_id: uuid.UUID, by: str
) -> ActionIntent:
    intent = session.get(ActionIntent, intent_id)
    if intent is None or intent.client_id != client_id:
        raise LookupError("intent not found in client scope")
    if intent.status not in {"awaiting_approval", "held"}:
        raise DeliveryError(
            f"intent is {intent.status!r}; only awaiting_approval/held can be approved"
        )
    intent.status, intent.approved_by = "approved", by
    append_audit(session, "intent.approved", {"intent_id": str(intent.id), "by": by}, client_id)
    jobs.enqueue(session, client_id, EXECUTE_JOB, str(intent.id), {"intent_id": str(intent.id)})
    return intent


def reject_intent(
    session: Session, client_id: uuid.UUID, intent_id: uuid.UUID, by: str, reason: str
) -> ActionIntent:
    intent = session.get(ActionIntent, intent_id)
    if intent is None or intent.client_id != client_id:
        raise LookupError("intent not found in client scope")
    if intent.status not in {"awaiting_approval", "held", "dry_run"}:
        raise DeliveryError(f"intent is {intent.status!r}; cannot reject")
    intent.status, intent.last_error = "rejected", f"rejected by {by}: {reason}"[:500]
    append_audit(session, "intent.rejected", {"intent_id": str(intent.id), "by": by}, client_id)
    return intent


# ----------------------------------------------------------------------------- job handler
def _stale_reason(session: Session, intent: ActionIntent) -> str | None:
    """Recovery case: the requirement changed between proposal and execution."""
    if intent.status not in {"approved", "held"} or intent.requirement_id is None:
        return None
    req = session.get(Requirement, intent.requirement_id)
    if req is None:
        return "requirement no longer exists"
    if req.status in {"superseded", "rejected", "cancelled", "blocked"}:
        return f"requirement became {req.status} before execution"
    draft = session.get(StoryDraft, intent.draft_id) if intent.draft_id else None
    if draft is not None and draft.requirement_version != req.current_version:
        return "requirement was revised after this proposal; re-draft and re-propose"
    return None


def make_execute_handler(settings: Settings, executor_factory: Any = None) -> Handler:
    factory = executor_factory or (lambda: build_executor(settings))

    async def handler(job: Job) -> None:
        session = object_session(job)
        assert session is not None
        intent = session.get(ActionIntent, uuid.UUID(job.payload["intent_id"]))
        if intent is None or intent.client_id != job.client_id:
            raise LookupError("intent not found in client scope")
        stale = _stale_reason(session, intent)
        if stale is not None:
            intent.status, intent.last_error = "cancelled", stale
            append_audit(
                session,
                "intent.cancelled_stale",
                {"intent_id": str(intent.id), "reason": stale},
                intent.client_id,
            )
            return
        executor = factory()
        executor.allowlist = effective_allowlist(session, settings)
        try:
            await executor.execute(
                session, intent, writes_allowed=not flags.kill_switch_on(session, settings)
            )
        finally:
            await executor.client.aclose()

    return handler
