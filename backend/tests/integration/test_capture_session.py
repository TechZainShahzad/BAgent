import time
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.capture.client import CaptureApiError, CaptureClient
from app.capture.session import CaptureSession
from app.capture.vad import SAMPLE_RATE
from app.config import Settings
from app.persistence.models import Meeting, OpenQuestion, TranscriptSegment
from app.providers.speech.base import SpeechSegment
from app.services.auth import create_user

PW = "a-long-test-password"
FRAME = 480


def tone(seconds, amp=0.2):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def quiet(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), np.float32)


def frames(signal):
    for i in range(0, len(signal), FRAME):
        yield signal[i : i + FRAME]


class FakeStt:
    """Maps each utterance to canned text by call order; records which channel it heard."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.channels = []

    def transcribe_array(self, samples, channel):
        self.channels.append(channel)
        text = self.texts.pop(0) if self.texts else "extra words here"
        return [SpeechSegment(start_s=0.0, end_s=1.0, text=text, channel=channel)]


@pytest.fixture
def env(factory, client_id):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    tc = TestClient(create_app(Settings(), factory))
    cc = CaptureClient()
    cc._http = tc  # route the companion through the in-process API
    cc.login(email, PW)
    return cc, str(client_id)


def test_session_streams_transcript_with_channels_and_no_invented_speakers(env, factory):
    cc, cid = env
    mid = cc.start(cid, "Live", "operator attests consent")
    stt = FakeStt(["we need an audit log", "agreed, add exports"])
    sess = CaptureSession(
        cc, cid, mid, stt, post_interval_s=0.2, heartbeat_interval_s=0.2, suggestion_interval_s=0.2
    )
    sess.add_source("loopback", frames(np.concatenate([quiet(0.3), tone(1.0), quiet(1.2)])))
    sess.add_source("mic", frames(np.concatenate([quiet(2.0), tone(1.0), quiet(1.2)])))
    sess.run(duration_s=3)
    assert sess.sent == 2 and not sess.errors
    with factory() as s:
        rows = list(
            s.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == uuid.UUID(mid))
                .order_by(TranscriptSegment.idx)
            )
        )
        assert sorted(r.channel for r in rows) == ["loopback", "mic"]
        assert all(r.speaker is None for r in rows)
        assert {r.text for r in rows} == {"we need an audit log", "agreed, add exports"}


def test_copilot_suggestion_reaches_operator_only_during_pause(env, factory):
    cc, cid = env
    mid = cc.start(cid, "Live", "attested")
    with factory() as s:
        s.add(
            OpenQuestion(
                client_id=uuid.UUID(cid),
                meeting_id=uuid.UUID(mid),
                question="Which SLA applies?",
                why_it_matters="scope",
                blocking=True,
            )
        )
        s.commit()
    shown = []
    sess = CaptureSession(
        cc,
        cid,
        mid,
        FakeStt(["we should talk about timing"]),
        on_suggestion=shown.append,
        post_interval_s=0.2,
        heartbeat_interval_s=0.2,
        suggestion_interval_s=0.3,
    )
    sess.add_source("loopback", frames(np.concatenate([tone(1.0), quiet(7.0)])))
    sess.run(duration_s=8)
    assert [q["text"] for q in shown] == ["Which SLA applies?"]  # exactly one, after speech stopped


def test_api_outage_buffers_transcript_and_reports_it(env, factory):
    cc, cid = env
    mid = cc.start(cid, "Live", "attested")
    real = cc.segments
    calls = {"n": 0}

    def flaky(client_id, meeting_id, segs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise CaptureApiError("simulated outage")
        return real(client_id, meeting_id, segs)

    cc.segments = flaky
    statuses = []
    sess = CaptureSession(
        cc,
        cid,
        mid,
        FakeStt(["important requirement text"]),
        on_status=statuses.append,
        post_interval_s=0.2,
        heartbeat_interval_s=5,
        suggestion_interval_s=5,
    )
    sess.add_source("loopback", frames(np.concatenate([tone(1.0), quiet(1.5)])))
    sess.run(duration_s=3)
    assert sess.sent == 1  # nothing lost
    assert any("unreachable" in s for s in statuses) and any(
        "reachable again" in s for s in statuses
    )


def test_dead_audio_device_is_surfaced(env):
    cc, cid = env
    mid = cc.start(cid, "Live", "attested")

    def dying():
        yield quiet(0.1)
        raise OSError("device unplugged")

    statuses = []
    sess = CaptureSession(
        cc,
        cid,
        mid,
        FakeStt([]),
        on_status=statuses.append,
        post_interval_s=0.2,
        heartbeat_interval_s=5,
        suggestion_interval_s=5,
    )
    sess.add_source("mic", dying())
    sess.run(duration_s=2)
    assert any("AUDIO SOURCE FAILED" in s for s in statuses) and sess.errors


def test_silent_companion_makes_meeting_degraded(env, factory):
    cc, cid = env
    mid = cc.start(cid, "Live", "attested")
    from datetime import UTC, datetime, timedelta

    from app.services import live

    with factory() as s:
        live.mark_stale_degraded(s, now=datetime.now(UTC) + timedelta(seconds=60))
        s.commit()
        m = s.get(Meeting, uuid.UUID(mid))
        assert m.status == "degraded" and "silent" in m.degraded_reason
    time.sleep(0)  # (state asserted from DB; no wall-clock dependence)


def test_start_without_consent_is_refused_by_the_api(env):
    cc, cid = env
    with pytest.raises(CaptureApiError, match="412"):
        cc.start(cid, "Live", "   ")
