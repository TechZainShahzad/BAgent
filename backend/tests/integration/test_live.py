import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.config import Settings
from app.orchestration import jobs
from app.orchestration.runner import JobRunner
from app.persistence.models import Job, Meeting, OpenQuestion, Requirement, TranscriptSegment
from app.providers.llm.fake import FakeLLMProvider
from app.services import live
from app.services.auth import create_user
from app.services.handlers import build_handlers
from app.services.live import LiveError, LiveSegmentIn
from app.services.meetings import ANALYZE_JOB, ConsentRequiredError

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def seg(text, ch="loopback", start=0.0):
    return LiveSegmentIn(text=text, channel=ch, start_s=start, end_s=start + 2)


def start(factory, client_id, now=T0):
    with factory() as s:
        m = live.start_live_meeting(
            s, client_id, "Live call", "operator attests consent", "op", now=now
        )
        s.commit()
        return m.id


def add_question(factory, client_id, mid, text="Which SLA applies?", blocking=False):
    with factory() as s:
        q = OpenQuestion(
            client_id=client_id,
            meeting_id=mid,
            question=text,
            why_it_matters="w",
            blocking=blocking,
        )
        s.add(q)
        s.commit()
        return q.id


def test_consent_is_a_hard_gate(factory, client_id):
    with factory() as s, pytest.raises(ConsentRequiredError):
        live.start_live_meeting(s, client_id, "x", "  ", "op")


def test_segments_are_stored_with_channel_and_no_invented_speaker(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        n = live.add_segments(
            s,
            client_id,
            mid,
            [seg("hello there everyone", "mic"), seg("we need audit logs", "loopback"), seg("  ")],
            now=at(1),
        )
        s.commit()
        assert n == 2
        rows = list(
            s.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == mid)
                .order_by(TranscriptSegment.idx)
            )
        )
        assert [r.idx for r in rows] == [0, 1] and [r.channel for r in rows] == ["mic", "loopback"]
        assert all(r.speaker is None for r in rows)  # channel is not an identity


def test_cross_client_cannot_write_segments(factory, client_id):
    from app.persistence.models import Client

    mid = start(factory, client_id)
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        with pytest.raises(LookupError):
            live.add_segments(s, other.id, mid, [seg("x y z")])


def test_window_job_enqueued_every_n_segments_without_duplicates(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        live.add_segments(s, client_id, mid, [seg(f"line number {i}") for i in range(7)], now=at(1))
        s.commit()
        assert not s.scalars(
            select(Job).where(Job.client_id == client_id, Job.kind == live.WINDOW_JOB)
        ).all()
        live.add_segments(s, client_id, mid, [seg("line eight here")], now=at(2))
        live.add_segments(s, client_id, mid, [seg("line nine here")], now=at(3))
        s.commit()
        assert (
            len(
                s.scalars(
                    select(Job).where(Job.client_id == client_id, Job.kind == live.WINDOW_JOB)
                ).all()
            )
            == 1
        )


def test_suggestion_only_during_a_pause_and_once_per_cooldown(factory, client_id):
    mid = start(factory, client_id)
    add_question(factory, client_id, mid, "First?")
    add_question(factory, client_id, mid, "Second?")
    with factory() as s:
        live.add_segments(s, client_id, mid, [seg("someone is talking right now")], now=at(10))
        s.commit()
        busy = live.suggest_now(s, client_id, mid, now=at(11))
        assert busy.question is None and "speech in progress" in busy.reason
        ready = live.suggest_now(s, client_id, mid, now=at(14))
        assert ready.question is not None and ready.question.question == "First?"
        s.commit()
        live.heartbeat(s, client_id, mid, now=at(19))
        cooling = live.suggest_now(s, client_id, mid, now=at(20))
        assert cooling.question is None and "cooldown" in cooling.reason
        live.heartbeat(s, client_id, mid, now=at(49))
        nxt = live.suggest_now(s, client_id, mid, now=at(50))
        assert nxt.question.question == "Second?"  # exactly one at a time
        s.commit()
        live.heartbeat(s, client_id, mid, now=at(99))
        assert live.suggest_now(s, client_id, mid, now=at(100)).reason == "no open questions"


def test_dropped_stream_degrades_visibly_stops_questions_and_recovers(factory, client_id):
    mid = start(factory, client_id)
    add_question(factory, client_id, mid)
    with factory() as s:
        live.add_segments(s, client_id, mid, [seg("talking about requirements")], now=at(1))
        s.commit()
        live.mark_stale_degraded(s, now=at(10))  # within heartbeat window
        s.commit()
        assert s.get(Meeting, mid).status == "live"
        live.mark_stale_degraded(s, now=at(60))
        s.commit()
        m = s.get(Meeting, mid)
        assert m.status == "degraded" and "silent" in m.degraded_reason
        sug = live.suggest_now(s, client_id, mid, now=at(61))
        assert sug.question is None and sug.reason.startswith("degraded")
        # the stream comes back: segments flip it back to live, with an audit trail
        live.add_segments(s, client_id, mid, [seg("we are back on the call")], now=at(70))
        s.commit()
        assert s.get(Meeting, mid).status == "live" and s.get(Meeting, mid).degraded_reason is None


def test_heartbeat_keeps_meeting_alive(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        live.heartbeat(s, client_id, mid, now=at(15))
        s.commit()
        live.mark_stale_degraded(s, now=at(30))
        s.commit()
        assert s.get(Meeting, mid).status == "live"  # heartbeat at 15s keeps it alive
        live.mark_stale_degraded(s, now=at(60))
        s.commit()
        assert s.get(Meeting, mid).status == "degraded"


def test_end_enqueues_final_analysis_and_blocks_further_segments(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        live.add_segments(s, client_id, mid, [seg("final words on the topic")], now=at(1))
        m = live.end_meeting(s, client_id, mid, now=at(2))
        s.commit()
        assert m.status == "queued" and m.ended_at is not None
        assert s.scalars(
            select(Job).where(Job.client_id == client_id, Job.kind == ANALYZE_JOB)
        ).one()
        with pytest.raises(LiveError):
            live.add_segments(s, client_id, mid, [seg("too late for this")])
        with pytest.raises(LiveError):
            live.end_meeting(s, client_id, mid)


def test_ending_with_nothing_captured_is_a_visible_failure(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        m = live.end_meeting(s, client_id, mid)
        assert m.status == "failed" and m.failure_reason == "no transcript was captured"
        assert not s.scalars(
            select(Job).where(Job.client_id == client_id, Job.kind == ANALYZE_JOB)
        ).all()


WINDOW_OUT = {
    "requirements": [
        {
            "title": "Audit log",
            "statement": "Audit everything",
            "kind": "functional",
            "explicit": True,
            "actors": [],
            "segment_index": 0,
            "quote": "we need audit logs",
        }
    ],
    "open_questions": [
        {"question": "What must the audit log cover?", "why_it_matters": "scope", "blocking": True}
    ],
}


async def test_window_analysis_adds_questions_but_never_persists_requirements(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        live.add_segments(
            s, client_id, mid, [seg("we need audit logs", start=i) for i in range(8)], now=at(1)
        )
        s.commit()
    prov = FakeLLMProvider(lambda r: WINDOW_OUT)
    runner = JobRunner(factory, build_handlers(Settings(), prov, None), client_id=client_id)
    assert await runner.run_until_empty() == 1
    with factory() as s:
        qs = list(s.scalars(select(OpenQuestion).where(OpenQuestion.client_id == client_id)))
        assert [q.question for q in qs] == ["What must the audit log cover?"]
        assert not list(s.scalars(select(Requirement).where(Requirement.client_id == client_id)))


async def test_full_live_flow_ends_in_final_analysis_and_transcript_purge(factory, client_id):
    mid = start(factory, client_id)
    with factory() as s:
        live.add_segments(s, client_id, mid, [seg("we need audit logs for every login")], now=at(1))
        live.end_meeting(s, client_id, mid, now=at(2))
        s.commit()
    prov = FakeLLMProvider(lambda r: WINDOW_OUT)
    runner = JobRunner(
        factory, build_handlers(Settings(transcript_grace_hours=0), prov, None), client_id=client_id
    )
    await runner.run_until_empty()
    with factory() as s:
        m = s.get(Meeting, mid)
        assert m.status == "analyzed" and m.transcript_purged_at is not None
        assert (
            s.scalars(select(Requirement).where(Requirement.client_id == client_id)).one().title
            == "Audit log"
        )
        assert not s.scalars(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == mid)
        ).all()


def test_mark_question_actions(factory, client_id):
    mid = start(factory, client_id)
    q1, q2 = (
        add_question(factory, client_id, mid, "A one?"),
        add_question(factory, client_id, mid, "B two?"),
    )
    with factory() as s:
        assert live.mark_question(s, client_id, q1, "answered").status == "answered"
        assert live.mark_question(s, client_id, q2, "skipped").status == "dropped"
        with pytest.raises(ValueError):
            live.mark_question(s, client_id, q1, "bogus")
        with pytest.raises(LookupError):
            live.mark_question(s, uuid.uuid4(), q1, "answered")


def test_live_api_roundtrip_and_auth(factory, client_id):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, "a-long-test-password")
        s.commit()
    c = TestClient(create_app(Settings(), factory))
    assert (
        c.post(
            f"/clients/{client_id}/live-meetings", json={"title": "t", "consent_basis": "x"}
        ).status_code
        == 401
    )
    csrf = c.post("/auth/login", json={"email": email, "password": "a-long-test-password"}).json()[
        "csrf_token"
    ]
    c.headers.update({"X-CSRF-Token": csrf})
    no_consent = c.post(
        f"/clients/{client_id}/live-meetings", json={"title": "t", "consent_basis": " "}
    )
    assert no_consent.status_code == 412
    r = c.post(
        f"/clients/{client_id}/live-meetings", json={"title": "Call", "consent_basis": "attested"}
    )
    assert r.status_code == 201
    mid = r.json()["id"]
    got = c.post(
        f"/clients/{client_id}/live-meetings/{mid}/segments",
        json={"segments": [{"text": "we need export to csv", "channel": "loopback", "start_s": 1}]},
    )
    assert got.json() == {"accepted": 1}
    assert c.post(f"/clients/{client_id}/live-meetings/{mid}/heartbeat").json()["status"] == "live"
    sug = c.get(f"/clients/{client_id}/live-meetings/{mid}/suggestion").json()
    assert sug["question"] is None
    ended = c.post(f"/clients/{client_id}/live-meetings/{mid}/end").json()
    assert ended["status"] == "queued"
    assert c.post(f"/clients/{client_id}/live-meetings/{mid}/end").status_code == 409
    assert c.post(f"/clients/{uuid.uuid4()}/live-meetings/{mid}/heartbeat").status_code == 404
    assert any(m["id"] == mid for m in c.get(f"/clients/{client_id}/meetings").json())


def test_worker_registry_contains_every_job_kind():
    h = build_handlers(Settings(), FakeLLMProvider(), None)
    assert set(h) == {
        ANALYZE_JOB,
        "jira.execute_intent",
        live.WINDOW_JOB,
        live.PLAN_JOB,
        "interview.warm",
        "autopilot.deliver",
    }
    assert jobs.Handler  # registry type is the durable handler contract
