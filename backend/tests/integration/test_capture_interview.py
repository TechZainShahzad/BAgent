import threading
import time
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.interviewer import OPENING
from app.api.main import create_app
from app.capture.client import CaptureClient
from app.capture.session import CaptureSession
from app.capture.vad import SAMPLE_RATE
from app.config import Settings
from app.persistence.models import Meeting, TranscriptSegment
from app.services.auth import create_user

PW = "a-long-test-password"


class FakeVoice:
    def __init__(self, seconds=0.4):
        self.said = []
        self.speaking = threading.Event()
        self.seconds = seconds

    def speak(self, text, cancel=None):
        self.speaking.set()
        try:
            self.said.append(text)
            time.sleep(self.seconds)
            return True
        finally:
            self.speaking.clear()


class FakeStt:
    def transcribe_array(self, samples, channel):
        return []


@pytest.fixture
def env(factory, client_id):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    cc = CaptureClient()
    cc._http = TestClient(create_app(Settings(), factory))
    cc.login(email, PW)
    return cc, str(client_id)


def tone(seconds, amp=0.2):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def frames(sig):
    for i in range(0, len(sig), 480):
        yield sig[i : i + 480]


def test_opening_is_spoken_once_and_recorded_as_agent_channel(env, factory):
    cc, cid = env
    mid = cc.start(cid, "Kickoff", "operator attests consent", {"mode": "interview"})
    voice = FakeVoice()
    statuses = []
    sess = CaptureSession(
        cc,
        cid,
        mid,
        FakeStt(),
        voice=voice,
        on_status=statuses.append,
        post_interval_s=0.2,
        heartbeat_interval_s=0.3,
        utterance_poll_s=0.1,
    )
    sess.add_source("loopback", frames(np.zeros(SAMPLE_RATE * 3, np.float32)))
    sess.run(duration_s=3)
    assert voice.said == [OPENING]  # spoken exactly once; nothing planned afterwards
    assert sess.spoken == 1 and not sess.errors
    with factory() as s:
        rows = list(
            s.scalars(
                select(TranscriptSegment).where(TranscriptSegment.meeting_id == uuid.UUID(mid))
            )
        )
        assert [(r.channel, r.speaker) for r in rows] == [("agent", "BA assistant")]
        assert s.get(Meeting, uuid.UUID(mid)).interview_state["opened"] is True


def test_capture_is_muted_while_the_agent_speaks_so_it_never_hears_itself(env):
    cc, cid = env
    mid = cc.start(cid, "K", "attested", {"mode": "interview"})
    sess = CaptureSession(cc, cid, mid, FakeStt(), voice=FakeVoice())
    sess._muted.set()
    sess._read("loopback", frames(np.concatenate([tone(1.5), np.zeros(SAMPLE_RATE, np.float32)])))
    assert sess._utts.empty()  # speech played while muted never becomes an utterance
    sess._muted.clear()
    sess._mute_until = 0.0
    sess._read("loopback", frames(np.concatenate([tone(1.5), np.zeros(SAMPLE_RATE, np.float32)])))
    assert not sess._utts.empty()


def test_voice_failure_is_surfaced_not_swallowed(env):
    cc, cid = env
    mid = cc.start(cid, "K", "attested", {"mode": "interview"})

    class BrokenVoice(FakeVoice):
        def speak(self, text, cancel=None):
            raise RuntimeError("output device unplugged")

    statuses = []
    sess = CaptureSession(
        cc,
        cid,
        mid,
        FakeStt(),
        voice=BrokenVoice(),
        on_status=statuses.append,
        utterance_poll_s=0.1,
        heartbeat_interval_s=5,
    )
    sess.add_source("loopback", frames(np.zeros(SAMPLE_RATE, np.float32)))
    sess.run(duration_s=2)
    assert any("VOICE FAILED" in s for s in statuses) and sess.errors


def test_end_signal_from_server_stops_the_session(env, factory):
    cc, cid = env
    mid = cc.start(cid, "K", "attested", {"mode": "interview"})
    from datetime import UTC, datetime, timedelta

    from app.services import interview

    with factory() as s:
        interview.record_agent_said(s, uuid.UUID(cid), uuid.UUID(mid), OPENING, "open", "intro")
        interview.record_agent_said(
            s,
            uuid.UUID(cid),
            uuid.UUID(mid),
            "Thank you, goodbye.",
            "close",
            "other",
            now=datetime.now(UTC) - timedelta(seconds=60),
        )
        s.commit()
    sess = CaptureSession(
        cc, cid, mid, FakeStt(), voice=FakeVoice(), utterance_poll_s=0.1, heartbeat_interval_s=0.2
    )
    sess.add_source("loopback", frames(np.zeros(SAMPLE_RATE * 5, np.float32)))
    sess.run(duration_s=8)
    assert sess.end_requested is True


def test_tts_renders_real_speech_on_this_machine():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows TTS only")
    from app.capture.voice import synthesize

    samples, sr = synthesize("Hello there, this is a test.")
    assert sr >= 8000 and len(samples) / sr > 0.8 and float(np.abs(samples).max()) > 0.05
