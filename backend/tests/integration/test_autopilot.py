import uuid

from sqlalchemy import select

from app.config import Settings
from app.integrations.jira.client import JiraClient
from app.integrations.jira.execution import JiraExecutor
from app.orchestration.runner import JobRunner
from app.persistence.models import (
    ActionIntent,
    Client,
    JiraIssueMapping,
    Meeting,
    Project,
    Requirement,
)
from app.providers.llm.fake import FakeLLMProvider
from app.services import live
from app.services.handlers import build_handlers
from app.services.live import LiveSegmentIn
from tests.support.fake_jira import FakeJira

FAKE_ISSUE_TYPES = {"Epic": "10001", "Story": "10004"}


async def no_sleep(_):
    return None


def executor_factory(fake):
    def make():
        c = JiraClient("https://x.atlassian.net", "a@b.c", "tok", transport=fake.transport())
        return JiraExecutor(c, {"SCRUM"}, sleep=no_sleep, poll_attempts=2)

    return make


def settings(mode="autonomous"):
    return Settings(autonomy_mode=mode, jira_project_allowlist="SCRUM", kill_switch=False)


GOAL = {
    "title": "School portal",
    "statement": "A web portal for parents, teachers and admins",
    "kind": "goal",
    "explicit": True,
    "actors": [],
    "segment_index": 1,
    "quote": "I want a school portal for parents and teachers",
}
GRADES = {
    "title": "Parents see grades",
    "statement": "Parents can see their child's grades after login",
    "kind": "functional",
    "explicit": True,
    "actors": ["Parent"],
    "segment_index": 2,
    "quote": "parents can see their child's grades after logging in",
    "confirmation_segment_index": 3,
}
FAST = {
    "title": "Fast and easy",
    "statement": "The portal should be fast and easy",
    "kind": "functional",
    "explicit": True,
    "actors": [],
    "segment_index": 4,
    "quote": "it must be fast and easy to use",
}


SPEED = {
    "title": "Speed",
    "statement": "The portal should be fast",
    "kind": "nfr",
    "explicit": True,
    "actors": [],
    "segment_index": 4,
    "quote": "it must be fast and easy to use",
}
BUDGET = {
    "title": "Modest budget",
    "statement": "The budget is modest, so focus on the essentials first",
    "kind": "constraint",
    "explicit": True,
    "actors": [],
    "segment_index": 4,
    "quote": "it must be fast and easy to use",
}


def llm_for(draft_for_fast=None):
    def respond(req):
        if req.agent == "requirements_analyst":
            return {"requirements": [GOAL, GRADES, FAST, BUDGET, SPEED], "open_questions": []}
        if req.agent == "story_architect":
            if "kind: goal" in req.user_prompt:
                return {
                    "issue_type": "Story",  # the model picks wrongly; code must coerce to Epic
                    "summary": "School portal for parents, teachers and admins",
                    "description": "Overall scope of the school portal.",
                    "acceptance_criteria": [],
                    "technical_notes": ["Role-based access: parent, teacher, admin"],
                    "assumptions_to_confirm": ["Single school, not multi-tenant"],
                }
            if "title: Fast and easy" in req.user_prompt:
                return draft_for_fast or {
                    "issue_type": "Story",
                    "summary": "Portal is fast and easy",
                    "description": "Make it fast.",
                    "acceptance_criteria": ["The portal should be fast", "Easy to use"],
                }
            return {
                "issue_type": "Story",
                "summary": "Parents view their child's grades after login",
                "description": "Parents log in and see grades.",
                "acceptance_criteria": [
                    "A logged-in parent sees only their own child's grades",
                    "A parent who is not logged in is redirected to the login page",
                ],
                "technical_notes": ["Grades table keyed by student; parent-student link"],
            }
        if req.agent == "conflict_detector":
            return {"relation": "unrelated", "explanation": "different topics"}
        raise AssertionError(req.agent)

    return respond


def seed(factory, client_id, autopilot=True):
    with factory() as s:
        p = Project(client_id=client_id, name="School", jira_project_key="SCRUM")
        s.add(p)
        s.flush()
        m = live.start_live_meeting(
            s,
            client_id,
            "Kickoff",
            "operator attests consent",
            "op",
            project_id=p.id,
            mode="interview",
            autopilot=autopilot,
            remote_name="Zain",
            remote_role="Client sponsor",
            remote_authority=True,
        )
        s.flush()
        live.add_segments(s, client_id, m.id, [])  # no-op safety
        mid = m.id
        s.commit()

    def say(text, ch="loopback"):
        with factory() as s:
            live.add_segments(s, client_id, mid, [LiveSegmentIn(text=text, channel=ch)])
            s.commit()

    def agent(text, action="ask"):
        from app.services import interview

        with factory() as s:
            interview.record_agent_said(s, client_id, mid, text, action, "x")
            s.commit()

    agent("Hello, I am an AI business analyst.", "open")  # 0
    say("I want a school portal for parents and teachers")  # 1 ... wait: indexes below
    return mid, say, agent


def build_meeting(factory, client_id, autopilot=True):
    mid, say, agent = seed(factory, client_id, autopilot)
    # indexes: 0 agent open, 1 client goal
    agent(
        "So parents can see their child's grades after logging in, is that right?", "confirm_back"
    )  # 2
    say("Yes, exactly.")  # 3
    say("Also it must be fast and easy to use")  # 4  (quote below matches this text)
    with factory() as s:
        live.end_meeting(s, client_id, mid)
        s.commit()
    return mid


async def run_all(factory, client_id, fake, llm):
    runner = JobRunner(
        factory,
        build_handlers(settings(), llm, None, executor_factory(fake)),
        client_id=client_id,
    )
    return await runner.run_until_empty()


async def test_epic_first_then_linked_stories_and_honest_hold_for_vague(factory, client_id):
    fake = FakeJira()
    mid = build_meeting(factory, client_id)
    await run_all(factory, client_id, fake, FakeLLMProvider(llm_for()))

    issues = sorted(fake.issues.values(), key=lambda i: int(i["id"]))
    assert len(issues) == 2  # epic + the one measurable story; NFR held, constraint folded in
    epic, story = issues
    assert epic["fields"]["issuetype"]["id"] == FAKE_ISSUE_TYPES["Epic"]  # coerced from "Story"
    assert "parent" not in epic["fields"]
    assert story["fields"]["issuetype"]["id"] == FAKE_ISSUE_TYPES["Story"]
    assert story["fields"]["parent"] == {"key": epic["key"]}  # linked under the epic
    for i in issues:
        assert any(label.startswith("ba-intent-") for label in i["fields"]["labels"])

    desc = str(story["fields"]["description"])
    assert "Technical considerations (assistant-suggested, not stated by the client)" in desc
    assert "Acceptance criteria" in desc
    assert "To confirm with the client" in str(epic["fields"]["description"])
    epic_desc = str(epic["fields"]["description"])
    assert "Constraints, quality targets and success measures" in epic_desc
    assert "The budget is modest" in epic_desc  # constraints live in the epic, not as stories

    with factory() as s:
        m = s.get(Meeting, mid)
        assert m.status == "analyzed"
        report = m.interview_state["autopilot_report"]
        by_title = {d["requirement"]: d for d in report["delivered"]}
        assert by_title["School portal"]["jira_key"] == epic["key"]
        assert by_title["Parents see grades"]["jira_key"] == story["key"]
        held = by_title["Fast and easy"]
        assert held["result"] == "held_needs_clarification" and any(
            "vague" in r for r in held["reasons"]
        )
        assert set(report["confirmed"]) == {
            "School portal",
            "Parents see grades",
            "Fast and easy",
            "Modest budget",
            "Speed",
        }
        maps = s.scalars(
            select(JiraIssueMapping).where(JiraIssueMapping.client_id == client_id)
        ).all()
        assert {x.jira_key for x in maps} == {epic["key"], story["key"]}


async def test_autopilot_is_idempotent_on_retry(factory, client_id):
    fake = FakeJira()
    mid = build_meeting(factory, client_id)
    llm = FakeLLMProvider(llm_for())
    await run_all(factory, client_id, fake, llm)
    before = len(fake.issues)
    # the job is re-delivered (crash/duplicate): nothing may be created twice
    from app.orchestration import jobs

    with factory() as s:
        jobs.enqueue(s, client_id, "autopilot.deliver", f"{mid}:again", {"meeting_id": str(mid)})
        s.commit()
    await run_all(factory, client_id, fake, llm)
    assert len(fake.issues) == before == 2
    with factory() as s:
        report = s.get(Meeting, mid).interview_state["autopilot_report"]
        results = {d["requirement"]: d["result"] for d in report["delivered"]}
        assert results["School portal"] == "already_in_jira"
        assert results["Parents see grades"] == "already_in_jira"


async def test_copilot_meeting_without_autopilot_never_touches_jira(factory, client_id):
    fake = FakeJira()
    build_meeting(factory, client_id, autopilot=False)
    await run_all(factory, client_id, fake, FakeLLMProvider(llm_for()))
    assert fake.write_calls == [] and not fake.issues
    with factory() as s:
        reqs = list(s.scalars(select(Requirement).where(Requirement.client_id == client_id)))
        assert reqs and all(r.status == "extracted" for r in reqs)  # nothing auto-confirmed


async def test_kill_switch_stops_autopilot_writes(factory, client_id):
    from app.services import flags

    fake = FakeJira()
    build_meeting(factory, client_id)
    with factory() as s:
        flags.set_kill_switch(s, True, "tester")
        s.commit()
    try:
        await run_all(factory, client_id, fake, FakeLLMProvider(llm_for()))
        assert not fake.issues
        with factory() as s:
            intents = list(
                s.scalars(select(ActionIntent).where(ActionIntent.client_id == client_id))
            )
            assert all(i.status in {"held"} for i in intents) or not intents
    finally:
        with factory() as s:
            flags.set_kill_switch(s, False, "tester")
            s.commit()


async def test_unverified_sponsor_cannot_be_auto_confirmed(factory, client_id):
    """If the operator did not declare authority, autopilot must not confirm or push."""
    fake = FakeJira()
    with factory() as s:
        p = Project(client_id=client_id, name="School", jira_project_key="SCRUM")
        s.add(p)
        s.flush()
        m = live.start_live_meeting(
            s,
            client_id,
            "K",
            "attested",
            "op",
            project_id=p.id,
            mode="interview",
            autopilot=True,
            remote_name="Zain",
            remote_authority=False,
        )
        mid = m.id
        s.commit()
    with factory() as s:
        live.add_segments(
            s,
            client_id,
            mid,
            [
                LiveSegmentIn(
                    text="I want a school portal for parents and teachers", channel="loopback"
                )
            ],
        )
        live.end_meeting(s, client_id, mid)
        s.commit()

    def llm(req):
        if req.agent == "requirements_analyst":
            g = {**GOAL, "segment_index": 0}
            return {"requirements": [g], "open_questions": []}
        return {"relation": "unrelated", "explanation": "x"}

    await run_all(factory, client_id, fake, FakeLLMProvider(llm))
    assert not fake.issues
    with factory() as s:
        report = s.get(Meeting, mid).interview_state["autopilot_report"]
        assert (
            report["not_confirmed"]
            and "verified stakeholder" in report["not_confirmed"][0]["reason"]
        )


def test_other_clients_meetings_are_untouched(factory, client_id):
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.commit()
        assert s.get(Client, other.id) is not None
