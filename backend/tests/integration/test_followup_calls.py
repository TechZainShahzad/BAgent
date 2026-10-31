import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agents import interviewer
from app.agents.interviewer import OPENING, PriorKnowledge, welcome_back
from app.config import Settings
from app.orchestration.runner import JobRunner
from app.persistence.models import (
    Client,
    JiraIssueMapping,
    Meeting,
    OpenQuestion,
    Project,
    Requirement,
)
from app.providers.llm.fake import FakeLLMProvider
from app.services import interview, live
from app.services.context import build_prior
from app.services.handlers import build_handlers
from app.services.live import LiveSegmentIn
from tests.integration.test_autopilot import GOAL, executor_factory
from tests.support.fake_jira import FakeJira

T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def at(s):
    return T0 + timedelta(seconds=s)


def seed_history(factory, client_id, with_jira=True):
    """One finished earlier call: goal + two requirements, one open question, covered topics."""
    with factory() as s:
        p = Project(client_id=client_id, name="School", jira_project_key="SCRUM")
        s.add(p)
        s.flush()
        m = Meeting(
            client_id=client_id,
            project_id=p.id,
            title="Kickoff",
            source="local_capture",
            status="analyzed",
            mode="interview",
            consent_status="recorded",
            interview_state={"covered": ["purpose", "users"]},
        )
        s.add(m)
        s.flush()
        goal = Requirement(
            client_id=client_id,
            project_id=p.id,
            meeting_id=m.id,
            title="School portal",
            statement="A portal for parents",
            kind="goal",
            explicit=True,
            status="ready_for_delivery",
        )
        grades = Requirement(
            client_id=client_id,
            project_id=p.id,
            meeting_id=m.id,
            title="Parents see grades",
            statement="Parents see grades after login",
            kind="functional",
            explicit=True,
            status="confirmed",
        )
        gone = Requirement(
            client_id=client_id,
            project_id=p.id,
            meeting_id=m.id,
            title="Rejected idea",
            statement="x",
            kind="functional",
            explicit=True,
            status="rejected",
        )
        s.add_all([goal, grades, gone])
        s.flush()
        if with_jira:
            for req, key in ((goal, "SCRUM-15"), (grades, "SCRUM-16")):
                from app.persistence.models import ActionIntent

                iid = uuid.uuid4()
                s.add(
                    ActionIntent(
                        id=iid,
                        client_id=client_id,
                        requirement_id=req.id,
                        kind="create_issue",
                        status="succeeded",
                        project_key="SCRUM",
                        marker=f"ba-intent-{iid.hex[:24]}",
                        jira_key=key,
                    )
                )
                s.flush()
                s.add(
                    JiraIssueMapping(
                        client_id=client_id,
                        requirement_id=req.id,
                        intent_id=iid,
                        project_key="SCRUM",
                        jira_key=key,
                        jira_id="1",
                    )
                )
        s.add(
            OpenQuestion(
                client_id=client_id,
                meeting_id=m.id,
                question="Which SMS provider?",
                why_it_matters="w",
                blocking=True,
            )
        )
        s.commit()
        return p.id, m.id


def test_new_client_has_no_prior_knowledge(factory, client_id):
    with factory() as s:
        assert build_prior(s, client_id) is None


def test_prior_knowledge_summarises_earlier_calls_scoped_and_clean(factory, client_id):
    pid, old_meeting = seed_history(factory, client_id)
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        s.add(
            Requirement(
                client_id=other.id,
                title="SECRET OTHER CLIENT",
                statement="s",
                kind="rule",
                explicit=True,
            )
        )
        s.commit()
        prior = build_prior(s, client_id, pid)
    assert prior is not None and prior.calls == 1 and prior.last_call_title == "Kickoff"
    assert prior.goal == "School portal" and prior.epic.startswith("SCRUM-15")
    assert any("Parents see grades" in r and "(SCRUM-16)" in r for r in prior.requirements)
    assert not any("Rejected idea" in r or "SECRET" in r for r in prior.requirements)
    assert prior.open_questions == ("[blocking] Which SMS provider?",)
    assert prior.covered_topics == ("purpose", "users")
    assert "Jira SCRUM" in (prior.project or "")


def test_the_current_meeting_is_never_part_of_its_own_prior(factory, client_id):
    pid, old = seed_history(factory, client_id)
    with factory() as s:
        assert build_prior(s, client_id, pid, exclude_meeting_id=old) is None  # nothing else exists


def test_welcome_back_opening_names_the_goal_and_keeps_the_disclosure():
    prior = PriorKnowledge(
        calls=2,
        last_call_title="Kickoff",
        last_call_date="01 Jun 2026",
        goal="School portal",
        requirements=("a", "b"),
        open_questions=("q",),
    )
    text = welcome_back(prior)
    assert "Hello again" in text and "School portal" in text and "2 requirement(s)" in text
    assert (
        "AI business analyst" in text and "written record" in text and "no audio is saved" in text
    )


def start_followup(factory, client_id, pid):
    with factory() as s:
        m = live.start_live_meeting(
            s,
            client_id,
            "Call 2",
            "operator attests consent",
            "op",
            project_id=pid,
            now=T0,
            mode="interview",
            remote_name="Zain",
            remote_authority=True,
            autopilot=True,
        )
        s.commit()
        return m.id


def test_returning_client_gets_welcome_back_new_client_gets_the_standard_opening(
    factory, client_id
):
    pid, _ = seed_history(factory, client_id)
    mid = start_followup(factory, client_id, pid)
    with factory() as s:
        out = interview.next_utterance(s, client_id, mid, now=at(1))
    assert "Hello again" in out["speak"] and "School portal" in out["speak"]
    assert out["reason"] == "opening (returning client)"
    with factory() as s:
        fresh = Client(name=f"n-{uuid.uuid4().hex[:8]}")
        s.add(fresh)
        s.flush()
        m = live.start_live_meeting(
            s, fresh.id, "First", "attested", "op", now=T0, mode="interview"
        )
        s.commit()
        assert interview.next_utterance(s, fresh.id, m.id, now=at(1))["speak"] == OPENING


def test_prompt_contains_earlier_knowledge_and_skips_already_covered_topics():
    prior = PriorKnowledge(
        calls=1,
        last_call_title="Kickoff",
        last_call_date="01 Jun 2026",
        goal="School portal",
        requirements=("[confirmed] Parents see grades: ... (SCRUM-16)",),
        open_questions=("[blocking] Which SMS provider?",),
        covered_topics=("purpose", "users"),
    )
    p = interviewer.build_prompt([], [], 0, 0, False, prior)
    assert (
        "EARLIER_CALLS: 1 earlier call(s)" in p
        and "Parents see grades" in p
        and "Which SMS provider?" in p
    )
    still = p.split("topics_still_to_cover:")[1].split("BEGIN_UNTRUSTED")[0]
    assert "- purpose:" not in still and "- users:" not in still and "- features:" in still
    assert (
        "FOLLOW-UP CALLS" in interviewer.SYSTEM_PROMPT
        and "Never claim you cannot remember" in interviewer.SYSTEM_PROMPT
    )
    assert "EARLIER_CALLS" not in interviewer.build_prompt([], [], 0, 0, False, None)


async def test_planner_gives_the_model_the_clients_history(factory, client_id):
    pid, _ = seed_history(factory, client_id)
    mid = start_followup(factory, client_id, pid)
    with factory() as s:
        interview.record_agent_said(s, client_id, mid, "Hello again", "open", "intro", now=at(1))
        live.add_segments(
            s,
            client_id,
            mid,
            [LiveSegmentIn(text="Do you remember our project?", channel="loopback")],
            now=at(5),
        )
        s.commit()
    seen = []

    def responder(req):
        seen.append(req.user_prompt)
        return {
            "action": "ask",
            "utterance": "Yes, last time we covered the parent grades.",
            "topic": "features",
            "covered_topics": [],
            "reasoning_summary": "recall",
        }

    runner = JobRunner(
        factory, build_handlers(Settings(), FakeLLMProvider(responder), None), client_id=client_id
    )
    await runner.run_until_empty()
    assert seen and "Parents see grades" in seen[0] and "EPIC_IN_JIRA: SCRUM-15" in seen[0]
    assert "Do you remember our project?" in seen[0]


async def test_followup_autopilot_reuses_the_existing_epic_and_attaches_new_stories(
    factory, client_id
):
    pid, old = seed_history(factory, client_id)
    mid = start_followup(factory, client_id, pid)
    fake = FakeJira()
    with factory() as s:
        interview.record_agent_said(s, client_id, mid, "Hello again", "open", "intro", now=at(1))
        live.add_segments(
            s,
            client_id,
            mid,
            [
                LiveSegmentIn(
                    text="I want a school portal for parents and teachers", channel="loopback"
                ),
                LiveSegmentIn(
                    text="Teachers must enter marks for every class each week", channel="loopback"
                ),
            ],
            now=at(5),
        )
        live.end_meeting(s, client_id, mid, now=at(9))
        s.commit()
    teachers = {
        "title": "Teachers enter marks",
        "statement": "Teachers enter marks for each class weekly",
        "kind": "functional",
        "explicit": True,
        "actors": ["Teacher"],
        "segment_index": 2,
        "quote": "Teachers must enter marks for every class each week",
    }

    def llm(req):
        if req.agent == "requirements_analyst":
            return {"requirements": [{**GOAL, "segment_index": 1}, teachers], "open_questions": []}
        if req.agent == "story_architect":
            return {
                "issue_type": "Story",
                "summary": "Teachers enter marks for each class every week",
                "description": "Teachers record marks weekly.",
                "acceptance_criteria": [
                    "A signed-in teacher can save marks for a student in their own class",
                    "A teacher cannot enter marks for a class they do not teach",
                ],
            }
        return {"relation": "unrelated", "explanation": "different"}

    runner = JobRunner(
        factory,
        build_handlers(
            Settings(autonomy_mode="autonomous", jira_project_allowlist="SCRUM"),
            FakeLLMProvider(llm),
            None,
            executor_factory(fake),
        ),
        client_id=client_id,
    )
    await runner.run_until_empty()
    created = list(fake.issues.values())
    assert len(created) == 1  # the story only: NO second epic
    assert created[0]["fields"]["issuetype"]["id"] == "10004"
    assert created[0]["fields"]["parent"] == {
        "key": "SCRUM-15"
    }  # attached to the epic from the first call
    with factory() as s:
        report = s.get(Meeting, mid).interview_state["autopilot_report"]
        by = {d["requirement"]: d for d in report["delivered"]}
        assert (
            by["School portal"]["result"] == "epic_already_exists"
            and by["School portal"]["jira_key"] == "SCRUM-15"
        )
        assert by["Teachers enter marks"]["result"].startswith("intent_succeeded")
        epics = s.scalars(
            select(JiraIssueMapping).where(
                JiraIssueMapping.client_id == client_id, JiraIssueMapping.project_key == "SCRUM"
            )
        ).all()
        assert len(epics) == 3  # SCRUM-15 + SCRUM-16 from before, plus the new story
