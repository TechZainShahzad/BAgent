import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.main import create_app
from app.config import Settings
from app.orchestration.runner import JobRunner
from app.persistence.models import Client, ConversationMessage, TranscriptSegment
from app.providers.llm.fake import FakeLLMProvider
from app.services import conversation, interview, knowledge, live, meetings
from app.services.auth import create_user
from app.services.handlers import build_handlers
from app.services.live import LiveSegmentIn

T0 = datetime(2026, 5, 4, 9, 30, tzinfo=UTC)


def at(s):
    return T0 + timedelta(seconds=s)


def live_call(factory, client_id):
    """A short interview: agent opens, client answers, agent confirms back, client says yes."""
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
            remote_role="Sponsor",
            remote_authority=True,
        )
        mid = m.id
        s.commit()

    def say(text, t):
        with factory() as s:
            live.add_segments(
                s,
                client_id,
                mid,
                [LiveSegmentIn(text=text, channel="loopback", start_s=t)],
                now=at(t),
            )
            s.commit()

    def agent(text, action, t):
        with factory() as s:
            interview.record_agent_said(s, client_id, mid, text, action, "x", now=at(t))
            s.commit()

    agent("Hello, I am an AI business analyst.", "open", 1)
    say("I want a school portal", 5)
    agent("Got it. So parents see grades, is that right?", "confirm_back", 9)
    say("Yes exactly", 14)
    return mid


def messages(factory, mid):
    with factory() as s:
        return list(
            s.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.meeting_id == mid)
                .order_by(ConversationMessage.idx)
            )
        )


def test_live_call_is_stored_in_order_with_roles_and_declared_speaker(factory, client_id):
    mid = live_call(factory, client_id)
    rows = messages(factory, mid)
    assert [(r.idx, r.role) for r in rows] == [
        (0, "agent"),
        (1, "client"),
        (2, "agent"),
        (3, "client"),
    ]
    assert rows[0].speaker == "BA assistant" and rows[1].speaker == "Zain"
    assert rows[3].text == "Yes exactly" and rows[0].said_at == at(1)


def test_conversation_survives_the_working_transcript_purge(factory, client_id):
    mid = live_call(factory, client_id)
    with factory() as s:
        live.end_meeting(s, client_id, mid, now=at(20))
        s.commit()

    def analyst(req):
        return {"requirements": [], "open_questions": []}

    import asyncio

    runner = JobRunner(
        factory,
        build_handlers(Settings(transcript_grace_hours=0), FakeLLMProvider(analyst), None),
        client_id=client_id,
    )
    asyncio.run(runner.run_until_empty())
    with factory() as s:
        # the working transcript is gone (privacy rule) ...
        assert not s.scalars(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == mid)
        ).all()
    # ... but the readable conversation remains for the dashboard
    assert len(messages(factory, mid)) == 4


def test_imported_transcript_file_becomes_a_conversation(factory, client_id, tmp_path: Path):
    f = tmp_path / "m.vtt"
    f.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Priya>We need an audit log.\n\n00:00:04.000 --> 00:00:06.000\n<v Tom>And exports to CSV.\n",
        encoding="utf-8",
    )
    with factory() as s:
        m = meetings.import_transcript(s, client_id, f, "Imported", "attested", "tester")
        s.commit()
        mid = m.id
    rows = messages(factory, mid)
    assert [(r.role, r.speaker, r.text) for r in rows] == [
        ("participant", "Priya", "We need an audit log."),
        ("participant", "Tom", "And exports to CSV."),
    ]


def test_storage_can_be_switched_off_and_nothing_is_kept(factory, client_id, monkeypatch):
    monkeypatch.setattr(conversation, "retention_days", lambda: 0)
    mid = live_call(factory, client_id)
    assert messages(factory, mid) == []
    with factory() as s:  # the working transcript still exists for analysis
        assert (
            len(
                s.scalars(
                    select(TranscriptSegment).where(TranscriptSegment.meeting_id == mid)
                ).all()
            )
            == 4
        )


def test_retention_job_deletes_expired_messages_only(factory, client_id):
    mid = live_call(factory, client_id)
    with factory() as s:
        s.execute(
            update(ConversationMessage)
            .where(ConversationMessage.meeting_id == mid, ConversationMessage.idx < 2)
            .values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        knowledge.purge_expired(s)
        s.commit()
    assert [r.idx for r in messages(factory, mid)] == [2, 3]


@pytest.fixture
def api(factory):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, "a-long-test-password")
        s.commit()
    c = TestClient(create_app(Settings(), factory))
    c.post("/auth/login", json={"email": email, "password": "a-long-test-password"})
    return c


def test_api_lists_calls_and_returns_the_chat_with_incremental_polling(api, factory, client_id):
    mid = live_call(factory, client_id)
    rows = api.get(f"/clients/{client_id}/conversations").json()
    row = next(r for r in rows if r["meeting_id"] == str(mid))
    assert row["messages"] == 4 and row["agent_messages"] == 2 and row["other_messages"] == 2
    full = api.get(f"/clients/{client_id}/meetings/{mid}/conversation").json()
    assert [m["role"] for m in full["messages"]] == ["agent", "client", "agent", "client"]
    assert full["meeting"]["title"] == "Kickoff" and full["total"] == 4
    tail = api.get(f"/clients/{client_id}/meetings/{mid}/conversation?after=1").json()
    assert [m["idx"] for m in tail["messages"]] == [2, 3]  # only what is new


def test_api_conversation_is_client_scoped_and_needs_login(api, factory, client_id):
    mid = live_call(factory, client_id)
    assert api.get(f"/clients/{uuid.uuid4()}/meetings/{mid}/conversation").status_code == 404
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.commit()
        other_id = other.id
    assert api.get(f"/clients/{other_id}/meetings/{mid}/conversation").status_code == 404
    assert all(
        r["meeting_id"] != str(mid) for r in api.get(f"/clients/{other_id}/conversations").json()
    )
    anon = TestClient(create_app(Settings(), factory))
    assert anon.get(f"/clients/{client_id}/conversations").status_code == 401
