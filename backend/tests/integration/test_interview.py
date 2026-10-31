import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.agents import interviewer
from app.agents.interviewer import OPENING, TOPICS
from app.config import Settings
from app.domain.requirement import ReqStatus
from app.orchestration.runner import JobRunner
from app.persistence.models import Evidence, Meeting, Requirement, Stakeholder, TranscriptSegment
from app.providers.llm.fake import FakeLLMProvider
from app.services import interview, knowledge, live
from app.services.handlers import build_handlers
from app.services.live import LiveSegmentIn

T0 = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)


def at(s):
    return T0 + timedelta(seconds=s)


def start(factory, client_id):
    with factory() as s:
        m = live.start_live_meeting(
            s,
            client_id,
            "Kickoff",
            "operator attests consent",
            "op",
            now=T0,
            mode="interview",
            remote_name="Zain",
            remote_role="Client sponsor",
            remote_authority=True,
        )
        s.commit()
        return m.id


def say(factory, client_id, mid, text, t):
    with factory() as s:
        live.add_segments(
            s, client_id, mid, [LiveSegmentIn(text=text, channel="loopback", start_s=t)], now=at(t)
        )
        s.commit()


def agent_said(factory, client_id, mid, text, action="ask", topic="purpose", t=1):
    with factory() as s:
        interview.record_agent_said(s, client_id, mid, text, action, topic, now=at(t))
        s.commit()


def nxt(factory, client_id, mid, t):
    with factory() as s:
        out = interview.next_utterance(s, client_id, mid, now=at(t))
        s.commit()
        return out


def keepalive(factory, client_id, mid, t):
    with factory() as s:
        live.heartbeat(s, client_id, mid, now=at(t))
        s.commit()


def view(factory, client_id, mid):
    with factory() as s:
        return interview.state_view(s, client_id, mid)


TURN = {
    "action": "ask",
    "utterance": "Who will use the portal: students, teachers, parents?",
    "topic": "users",
    "covered_topics": ["purpose"],
    "reasoning_summary": "next uncovered topic",
}


def plan_runner(factory, client_id, responder=lambda r: TURN):
    return JobRunner(
        factory, build_handlers(Settings(), FakeLLMProvider(responder), None), client_id=client_id
    )


def test_agent_opens_first_with_disclosure_then_waits(factory, client_id):
    mid = start(factory, client_id)
    first = nxt(factory, client_id, mid, 1)
    assert first["speak"] == OPENING
    assert "AI business analyst" in first["speak"] and "no audio is saved" in first["speak"]
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=2)
    keepalive(factory, client_id, mid, 3)
    assert nxt(factory, client_id, mid, 4)["speak"] is None  # nothing planned yet: it listens


def test_declared_speaker_is_recorded_as_declared_not_verified_by_voice(factory, client_id):
    mid = start(factory, client_id)
    say(factory, client_id, mid, "I want a school portal", 5)
    with factory() as s:
        seg = s.scalars(select(TranscriptSegment).where(TranscriptSegment.meeting_id == mid)).one()
        assert seg.speaker == "Zain" and seg.channel == "loopback"
        sh = s.scalars(
            select(Stakeholder).where(
                Stakeholder.client_id == client_id, Stakeholder.name == "Zain"
            )
        ).one()
        assert "not voice-verified" in sh.verification_note and sh.has_decision_authority


async def test_speaks_only_a_fresh_plan_after_a_pause_and_cooldown(factory, client_id):
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)
    say(factory, client_id, mid, "We want a school portal for parents and teachers", 10)
    assert await plan_runner(factory, client_id).run_until_empty() == 1  # plan job ran
    keepalive(factory, client_id, mid, 11)
    busy = nxt(factory, client_id, mid, 11)
    assert busy["speak"] is None and "waiting for a pause" in busy["reason"]
    keepalive(factory, client_id, mid, 12)
    ready = nxt(factory, client_id, mid, 13)
    assert ready["speak"] == TURN["utterance"] and ready["reason"] == "ready"
    agent_said(factory, client_id, mid, ready["speak"], ready["action"], ready["topic"], t=14)
    keepalive(factory, client_id, mid, 15)
    assert nxt(factory, client_id, mid, 15)["speak"] is None  # the plan was consumed


async def test_stale_plan_is_never_spoken_after_the_client_said_more(factory, client_id):
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)
    say(factory, client_id, mid, "We want a school portal", 10)
    await plan_runner(factory, client_id).run_until_empty()
    say(factory, client_id, mid, "Oh and it must also work on phones", 14)  # client kept talking
    keepalive(factory, client_id, mid, 18)
    out = nxt(factory, client_id, mid, 19)
    assert out["speak"] is None and "planning" in out["reason"]  # old plan discarded as stale


async def test_plan_for_superseded_count_is_skipped_without_an_llm_call(factory, client_id):
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)
    say(factory, client_id, mid, "first thing I want to say", 10)
    say(factory, client_id, mid, "and a second thing as well", 12)
    prov = FakeLLMProvider(lambda r: TURN)
    runner = JobRunner(factory, build_handlers(Settings(), prov, None), client_id=client_id)
    await runner.run_until_empty()
    assert len(prov.calls) == 1  # the job for count=1 was skipped; only count=2 asked the model


async def test_agent_lines_are_stored_as_agent_channel_and_covered_topics_accumulate(
    factory, client_id
):
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)
    say(factory, client_id, mid, "A portal for our school please", 10)
    await plan_runner(factory, client_id).run_until_empty()
    keepalive(factory, client_id, mid, 14)
    out = nxt(factory, client_id, mid, 14)
    agent_said(factory, client_id, mid, out["speak"], out["action"], out["topic"], t=15)
    with factory() as s:
        rows = list(
            s.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == mid)
                .order_by(TranscriptSegment.idx)
            )
        )
        assert [r.channel for r in rows] == ["agent", "loopback", "agent"]
        assert rows[2].speaker == "BA assistant"
        st = s.get(Meeting, mid).interview_state
        assert st["covered"] == ["purpose"] and st["turns"] == 1
    v = view(factory, client_id, mid)
    assert v["coverage"] == round(1 / len(TOPICS), 2) and "users" in v["remaining"]


def test_closing_then_end_signal_after_grace(factory, client_id):
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)
    agent_said(
        factory, client_id, mid, "Thank you, I will write this up now.", "close", "other", t=100
    )
    keepalive(factory, client_id, mid, 105)
    assert nxt(factory, client_id, mid, 105) == {"speak": None, "reason": "closing"}
    keepalive(factory, client_id, mid, 113)
    assert nxt(factory, client_id, mid, 113)["end"] is True


def test_degraded_stream_stops_the_agent_from_speaking(factory, client_id):
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)
    out = nxt(factory, client_id, mid, 200)  # no heartbeat for minutes
    assert out["speak"] is None and out["reason"].startswith("degraded")


def test_copilot_meetings_never_speak(factory, client_id):
    with factory() as s:
        m = live.start_live_meeting(s, client_id, "c", "attested", "op", now=T0)
        s.commit()
        assert interview.next_utterance(s, client_id, m.id, now=at(1))["speak"] is None


def test_cross_client_cannot_drive_the_agent(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s, pytest.raises(LookupError):
        interview.next_utterance(s, uuid.uuid4(), mid)
    with factory() as s, pytest.raises(LookupError):
        interview.record_agent_said(s, uuid.uuid4(), mid, "hi there", "ask", "x")


def test_forced_close_and_untrusted_delimiters_in_the_prompt():
    prompt = interviewer.build_prompt([], [], interview.MAX_TURNS, 0, True)
    assert "you must use action close now" in prompt
    assert "BEGIN_UNTRUSTED_CONVERSATION" in prompt


async def test_confirmed_proposal_becomes_evidence_and_declared_authority_confirms(
    factory, client_id
):
    """Agent proposes, client says yes, analysis keeps it, the declared sponsor confirms."""
    mid = start(factory, client_id)
    agent_said(factory, client_id, mid, OPENING, "open", "intro", t=1)  # idx 0
    say(factory, client_id, mid, "I want a school portal for parents and teachers", 10)  # idx 1
    agent_said(
        factory,
        client_id,
        mid,
        "So parents can see their child's grades after logging in, is that right?",
        "confirm_back",
        "features",
        t=14,
    )  # idx 2
    say(factory, client_id, mid, "Yes, exactly.", 20)  # idx 3
    with factory() as s:
        live.end_meeting(s, client_id, mid, now=at(30))
        s.commit()

    def analyst(req):
        return {
            "requirements": [
                {
                    "title": "School portal",
                    "statement": "A portal for parents and teachers",
                    "kind": "goal",
                    "explicit": True,
                    "actors": [],
                    "segment_index": 1,
                    "quote": "I want a school portal for parents and teachers",
                },
                {
                    "title": "Parents see grades",
                    "statement": "Parents can see their child's grades after login",
                    "kind": "functional",
                    "explicit": True,
                    "actors": ["Parent"],
                    "segment_index": 2,
                    "quote": "parents can see their child's grades after logging in",
                    "confirmation_segment_index": 3,
                },
                {
                    "title": "Invented",
                    "statement": "Parents pay fees",
                    "kind": "functional",
                    "explicit": True,
                    "actors": [],
                    "segment_index": 0,
                    "quote": "I will help you pay school fees online",
                },
            ],
            "open_questions": [],
        }

    runner = JobRunner(
        factory, build_handlers(Settings(), FakeLLMProvider(analyst), None), client_id=client_id
    )
    await runner.run_until_empty()
    with factory() as s:
        reqs = {
            r.title: r
            for r in s.scalars(select(Requirement).where(Requirement.client_id == client_id))
        }
        assert set(reqs) == {"School portal", "Parents see grades"}  # agent-only claim rejected
        ev = s.scalars(
            select(Evidence).where(Evidence.requirement_id == reqs["Parents see grades"].id)
        ).one()
        assert ev.confirmation_quote == "Yes, exactly."
        assert ev.attribution_source == "operator_declared"
        assert ev.speaker_label == "Zain" and ev.stakeholder_id is not None
        # declared sponsor with authority -> confirmation passes the same guards as always
        knowledge.transition_requirement(
            s, client_id, reqs["Parents see grades"].id, ReqStatus.CONFIRMED, "sponsor said yes"
        )
        s.commit()


async def test_opening_starts_one_warm_llm_session_scoped_to_client_and_meeting(factory, client_id):
    from app.persistence.models import Job

    mid = start(factory, client_id)
    nxt(factory, client_id, mid, 1)
    nxt(factory, client_id, mid, 2)  # polled again before the greeting finished: still one job
    with factory() as s:
        warm_jobs = list(
            s.scalars(select(Job).where(Job.kind == interview.WARM_JOB, Job.client_id == client_id))
        )
        assert len(warm_jobs) == 1 and warm_jobs[0].client_id == client_id
    llm = FakeLLMProvider(lambda r: TURN)
    handler = build_handlers(Settings(), llm, None)[interview.WARM_JOB]
    with factory() as s:  # run only THIS client's job (the test database is shared)
        await handler(s.get(Job, warm_jobs[0].id))
    sent = [r for r in llm.calls if r.agent == "interviewer"]
    assert [r.warm_key for r in sent] == [f"{client_id}:{mid}"]
