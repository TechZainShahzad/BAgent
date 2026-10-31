import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings
from app.orchestration import jobs
from app.services.auth import create_user

PW = "a-long-test-password"


@pytest.fixture
def api(factory):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    return TestClient(create_app(Settings(), factory)), email


def test_health_public(api):
    c, _ = api
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}  # no config leak
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"


def test_requires_auth(api):
    c, _ = api
    assert c.get("/clients").status_code == 401


def test_login_failure(api):
    c, email = api
    bad = c.post("/auth/login", json={"email": email, "password": "wrong-wrong-wrong"})
    assert bad.status_code == 401


def test_csrf_required_for_mutation(api):
    c, email = api
    r = c.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.post("/clients", json={"name": "x"}).status_code == 403
    assert (
        c.post("/clients", json={"name": "x"}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    )
    name = f"client-{uuid.uuid4().hex[:8]}"
    ok = c.post("/clients", json={"name": name}, headers={"X-CSRF-Token": r.json()["csrf_token"]})
    assert ok.status_code == 201
    assert name in [x["name"] for x in c.get("/clients").json()]
    assert c.get(f"/clients/{ok.json()['id']}/audit/verify").json()["valid"] is True


def test_failed_job_visible_via_api(api, factory, client_id):
    c, email = api
    c.post("/auth/login", json={"email": email, "password": PW})
    with factory() as s:
        jid, _ = jobs.enqueue(s, client_id, "k-api", "e1", max_attempts=1)
        job = jobs.claim(s, "w", ["k-api"])
        jobs.mark_failed_attempt(s, job, "RuntimeError: provider down")
        s.commit()
    rows = c.get(f"/clients/{client_id}/jobs").json()
    failed = next(r for r in rows if r["id"] == str(jid))
    assert failed["status"] == "failed" and "provider down" in failed["last_error"]


def test_login_is_throttled_after_repeated_failures(api):
    c, email = api
    for _ in range(5):
        assert (
            c.post(
                "/auth/login", json={"email": email, "password": "wrong-wrong-wrong"}
            ).status_code
            == 401
        )
    locked = c.post("/auth/login", json={"email": email, "password": PW})
    assert locked.status_code == 429  # even the right password is refused during lockout
    other = c.post("/auth/login", json={"email": "nobody@example.com", "password": "x" * 12})
    assert other.status_code == 401  # lockout is per account, not global


def test_created_is_only_reported_after_the_data_is_committed(factory):
    """201 must mean saved: a failing commit is a 500 and nothing is applied."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session, sessionmaker

    from app.persistence.models import Client

    class FlakySession(Session):
        fail = False

        def commit(self):
            if FlakySession.fail:
                FlakySession.fail = False  # the first real commit fails; later no-ops succeed
                raise RuntimeError("disk full")
            super().commit()

    flaky = sessionmaker(factory.kw["bind"], class_=FlakySession, expire_on_commit=False)
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    c = TestClient(create_app(Settings(), flaky))
    csrf = c.post("/auth/login", json={"email": email, "password": PW}).json()["csrf_token"]
    name = f"Flaky-{uuid.uuid4().hex[:6]}"
    FlakySession.fail = True
    try:
        r = c.post("/clients", json={"name": name}, headers={"X-CSRF-Token": csrf})
    finally:
        FlakySession.fail = False
    assert r.status_code == 500 and "nothing was applied" in r.json()["detail"]
    with factory() as s:
        assert s.scalar(select(Client).where(Client.name == name)) is None
    ok = c.post("/clients", json={"name": name}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201
    with factory() as s:  # committed BEFORE the response was handed back
        assert s.scalar(select(Client).where(Client.name == name)) is not None
