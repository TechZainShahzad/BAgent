import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.api.main import create_app
from app.config import Settings
from app.persistence.models import AuditEvent, Client, Project
from app.services import capture_runner
from app.services.auth import create_user
from app.services.capture_runner import (
    CaptureBusyError,
    CaptureManager,
    CaptureParams,
    build_command,
)

PW = "a-long-test-password"
# A stand-in for the real capture program: announces a meeting, runs until the stop file appears.
FAKE_CAPTURE = (
    "import sys, time, pathlib\n"
    "print('MEETING_ID 11111111-2222-3333-4444-555555555555', flush=True)\n"
    "print('[status] AGENT: hello', flush=True)\n"
    "stop = pathlib.Path(sys.argv[1])\n"
    "while not stop.exists(): time.sleep(0.05)\n"
    "print('stop requested; ending', flush=True)\n"
)
STUBBORN = "import time\nprint('MEETING_ID aaaa', flush=True)\nwhile True: time.sleep(0.1)\n"


def fake_builder(script=FAKE_CAPTURE):
    return lambda params, api, stop: [sys.executable, "-c", script, str(stop)]


def params(**kw):
    base = {"client_id": uuid.uuid4(), "title": "Kickoff", "consent_basis": "Operator consents"}
    base.update(kw)
    return CaptureParams(**base)


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------------ validation
def test_autopilot_needs_a_confirmable_speaker_and_a_spoken_interview():
    with pytest.raises(ValidationError, match="remote speaker"):
        params(autopilot=True)
    with pytest.raises(ValidationError, match="interview"):
        params(autopilot=True, mode="copilot", remote_name="Z", remote_authority=True)
    assert params(autopilot=True, remote_name="Z", remote_authority=True).autopilot


def test_other_invalid_forms_are_refused():
    with pytest.raises(ValidationError, match="audio source"):
        params(use_loopback=False, use_mic=False)
    with pytest.raises(ValidationError):
        params(consent_basis="")
    with pytest.raises(ValidationError, match="must not start"):
        params(agent_output_device="--password")
    with pytest.raises(ValidationError):
        params(duration_min=9999)


def test_command_is_an_argument_list_without_secrets(tmp_path):
    p = params(
        project_id=uuid.uuid4(),
        autopilot=True,
        remote_name="Zain",
        remote_role="Sponsor",
        remote_authority=True,
        agent_output_device="CABLE Input",
        agent_voice="Microsoft Zira Desktop",
        duration_min=30,
    )
    cmd = build_command(p, "http://127.0.0.1:8000", tmp_path / "x.stop")
    assert cmd[1:3] == ["-m", "app.cli.main"] and cmd[3] == "capture"
    for flag in ("--interview", "--autopilot", "--remote-authority", "--no-mic"):
        assert flag in cmd
    assert (
        cmd[cmd.index("--duration") + 1] == "1800"
        and cmd[cmd.index("--agent-output-device") + 1] == "CABLE Input"
    )
    assert not any(
        "cookie" in c.lower() or "password" in c.lower() for c in cmd
    )  # secrets go via env only


# ------------------------------------------------------------------ manager lifecycle
def test_start_status_stop_gracefully(tmp_path):
    m = CaptureManager(runs_dir=tmp_path, command_builder=fake_builder())
    assert m.status() == {"state": "idle"}
    m.start(params(), "http://x", "cookie", "csrf", "tester")
    assert wait_for(lambda: m.status().get("meeting_id") == "11111111-2222-3333-4444-555555555555")
    st = m.status()
    assert st["state"] == "running" and any("AGENT: hello" in line for line in st["log"])
    with pytest.raises(CaptureBusyError):
        m.start(params(), "http://x", "cookie", "csrf", "tester")  # one set of audio devices
    assert m.stop() is True
    assert wait_for(lambda: m.status()["state"] == "ended")
    st = m.status()
    assert st["exit_code"] == 0 and any("stop requested" in line for line in st["log"])
    assert m.stop() is False  # nothing left to stop
    m.start(params(title="Second"), "http://x", "cookie", "csrf", "tester")  # free again
    m.stop()
    wait_for(lambda: m.status()["state"] == "ended")


def test_a_capture_that_ignores_the_stop_request_is_terminated_after_the_grace_period(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(capture_runner, "STOP_GRACE_S", 0.3)
    m = CaptureManager(runs_dir=tmp_path, command_builder=fake_builder(STUBBORN))
    m.start(params(), "http://x", "c", "t", "tester")
    wait_for(lambda: m.status().get("meeting_id") == "aaaa")
    m.stop()
    time.sleep(0.5)
    assert wait_for(lambda: m.status()["state"] == "ended")
    assert m.status()["exit_code"] != 0


def test_secrets_reach_the_child_only_through_its_environment(tmp_path):
    seen = {}

    def launcher(cmd, env, log):
        seen["env"], seen["cmd"] = env, list(cmd)
        return capture_runner._default_launcher(cmd, env, log)

    m = CaptureManager(launcher=launcher, runs_dir=tmp_path, command_builder=fake_builder())
    m.start(params(), "http://x", "SESSION-COOKIE-VALUE", "CSRF-VALUE", "tester")
    assert (
        seen["env"]["BA_SESSION_COOKIE"] == "SESSION-COOKIE-VALUE"
        and "ANTHROPIC_API_KEY" not in seen["env"]
    )
    assert "SESSION-COOKIE-VALUE" not in " ".join(seen["cmd"])
    m.stop()
    wait_for(lambda: m.status()["state"] == "ended")


# ------------------------------------------------------------------ API
@pytest.fixture
def env(factory, client_id, tmp_path):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    seen = {}

    def launcher(cmd, e, log):
        seen["env"] = e
        return capture_runner._default_launcher(cmd, e, log)

    mgr = CaptureManager(launcher=launcher, runs_dir=tmp_path, command_builder=fake_builder())
    c = TestClient(create_app(Settings(), factory, capture_manager=mgr))
    csrf = c.post("/auth/login", json={"email": email, "password": PW}).json()["csrf_token"]
    c.headers.update({"X-CSRF-Token": csrf})
    yield c, mgr, seen, client_id
    mgr.stop()
    wait_for(lambda: mgr.status()["state"] in {"ended", "idle"})


def body(client_id, **kw):
    b = {"client_id": str(client_id), "title": "Kickoff", "consent_basis": "Operator consents"}
    b.update(kw)
    return b


def test_api_requires_login_and_csrf(factory, client_id, tmp_path):
    mgr = CaptureManager(runs_dir=tmp_path, command_builder=fake_builder())
    anon = TestClient(create_app(Settings(), factory, capture_manager=mgr))
    assert anon.get("/capture/status").status_code == 401
    assert anon.post("/capture/start", json=body(client_id)).status_code == 401


def test_api_start_status_stop_and_session_handoff(env, factory):
    c, mgr, seen, client_id = env
    assert c.get("/capture/status").json() == {"state": "idle"}
    r = c.post("/capture/start", json=body(client_id, remote_name="Zain", remote_authority=True))
    assert r.status_code == 201
    assert wait_for(lambda: c.get("/capture/status").json().get("meeting_id") is not None)
    # the cookie handed to the child is a working dashboard session for the same user
    child = TestClient(create_app(Settings(), factory))
    child.cookies.set("ba_session", seen["env"]["BA_SESSION_COOKIE"])
    assert child.get("/clients").status_code == 200
    assert c.post("/capture/start", json=body(client_id)).status_code == 409
    assert c.post("/capture/stop").json() == {"stopping": True}
    assert wait_for(lambda: c.get("/capture/status").json()["state"] == "ended")
    with factory() as s:
        ev = s.scalars(
            select(AuditEvent).where(
                AuditEvent.client_id == client_id,
                AuditEvent.event_type == "capture.started_from_dashboard",
            )
        ).all()
        assert len(ev) == 1


def test_api_rejects_unknown_client_other_clients_project_and_bad_forms(env, factory):
    c, _, _, client_id = env
    assert c.post("/capture/start", json=body(uuid.uuid4())).status_code == 404
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        p = Project(client_id=other.id, name="theirs")
        s.add(p)
        s.commit()
        pid = str(p.id)
    assert c.post("/capture/start", json=body(client_id, project_id=pid)).status_code == 404
    assert c.post("/capture/start", json=body(client_id, autopilot=True)).status_code == 422
    assert c.post("/capture/start", json=body(client_id, consent_basis="")).status_code == 422
    assert c.get("/capture/status").json() == {"state": "idle"}  # nothing was started


def test_options_and_device_tests_are_safe(env):
    c, mgr, _, client_id = env
    opts = c.get("/capture/options").json()
    assert {
        "available",
        "microphones",
        "loopback",
        "speakers",
        "voices",
        "cable_found",
    } <= opts.keys()
    assert c.post("/capture/test-voice", json={"device": "--x"}).status_code == 422
    c.post("/capture/start", json=body(client_id))
    wait_for(lambda: mgr.status()["state"] == "running")
    assert c.post("/capture/test-audio", json={}).status_code == 409  # devices busy during a call
