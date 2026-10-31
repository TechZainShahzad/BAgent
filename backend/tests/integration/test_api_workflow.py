import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.config import Settings
from app.persistence.models import (
    ActionIntent,
    Evidence,
    LLMCallLog,
    Meeting,
    Requirement,
    Stakeholder,
)
from app.providers.llm.base import LLMRequest, UsageLimitError
from app.providers.llm.fake import FakeLLMProvider
from app.services import flags
from app.services.auth import create_user
from app.services.llm_logging import LoggedLLMProvider

PW = "a-long-test-password"


@pytest.fixture
def api(factory):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    c = TestClient(create_app(Settings(), factory))
    csrf = c.post("/auth/login", json={"email": email, "password": PW}).json()["csrf_token"]
    c.headers.update({"X-CSRF-Token": csrf})
    return c, email


def seed_req(factory, client_id, status="extracted"):
    with factory() as s:
        m = Meeting(client_id=client_id, title="Kickoff", source="transcript_file")
        s.add(m)
        s.flush()
        sh = Stakeholder(client_id=client_id, name="Priya")
        s.add(sh)
        s.flush()
        r = Requirement(
            client_id=client_id,
            meeting_id=m.id,
            title="Reset",
            statement="reset by email",
            kind="functional",
            explicit=True,
            status=status,
        )
        s.add(r)
        s.flush()
        s.add(
            Evidence(
                client_id=client_id,
                meeting_id=m.id,
                requirement_id=r.id,
                segment_index=0,
                quote="reset by email please",
                speaker_label="Priya",
                stakeholder_id=sh.id,
                expires_at=datetime.now(UTC) + timedelta(days=5),
            )
        )
        s.commit()
        return r.id


def test_requirement_list_and_trace(api, factory, client_id):
    c, _ = api
    rid = seed_req(factory, client_id)
    rows = c.get(f"/clients/{client_id}/requirements").json()
    assert [r["id"] for r in rows] == [str(rid)]
    trace = c.get(f"/clients/{client_id}/requirements/{rid}").json()
    assert trace["evidence"][0]["quote"] == "reset by email please"
    assert trace["evidence"][0]["attribution_confidence"] is None
    assert trace["requirement"]["status"] == "extracted"


def test_trace_is_client_scoped(api, factory, client_id):
    c, _ = api
    rid = seed_req(factory, client_id)
    assert c.get(f"/clients/{uuid.uuid4()}/requirements/{rid}").status_code == 404


def test_guard_refusal_is_surfaced_as_409_with_reason(api, factory, client_id):
    c, _ = api
    rid = seed_req(factory, client_id)
    r = c.post(
        f"/clients/{client_id}/requirements/{rid}/transition",
        json={"target": "confirmed", "reason": "try"},
    )
    assert r.status_code == 409 and "verified stakeholder" in r.json()["detail"]


def test_stakeholder_verification_then_confirm(api, factory, client_id):
    c, _ = api
    rid = seed_req(factory, client_id)
    r = c.post(
        f"/clients/{client_id}/stakeholders",
        json={"name": "Priya", "has_decision_authority": True, "identity_verified": True},
    )
    assert r.status_code == 201
    ok = c.post(
        f"/clients/{client_id}/requirements/{rid}/transition",
        json={"target": "confirmed", "reason": "owner agreed"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"


def test_mutations_need_csrf(api, factory, client_id):
    c, _ = api
    rid = seed_req(factory, client_id)
    bad = c.post(
        f"/clients/{client_id}/requirements/{rid}/transition",
        json={"target": "rejected", "reason": "x"},
        headers={"X-CSRF-Token": "wrong"},
    )
    assert bad.status_code == 403


def test_unauthenticated_cannot_read_anything_new(factory, client_id):
    c = TestClient(create_app(Settings(), factory))
    for path in ("requirements", "meetings", "questions", "conflicts", "intents"):
        assert c.get(f"/clients/{client_id}/{path}").status_code == 401
    assert c.get("/control").status_code == 401


def test_intents_approve_reject_and_kill_switch(api, factory, client_id):
    c, email = api
    rid = seed_req(factory, client_id)
    with factory() as s:
        i = ActionIntent(
            client_id=client_id,
            requirement_id=rid,
            kind="create_issue",
            status="awaiting_approval",
            project_key="SCRUM",
            marker=f"ba-intent-{uuid.uuid4().hex[:24]}",
            payload={"summary": "S"},
        )
        j = ActionIntent(
            client_id=client_id,
            requirement_id=rid,
            kind="create_issue",
            status="awaiting_approval",
            project_key="SCRUM",
            marker=f"ba-intent-{uuid.uuid4().hex[:24]}",
            payload={"summary": "T"},
        )
        s.add_all([i, j])
        s.commit()
        iid, jid = i.id, j.id
    listing = c.get(f"/clients/{client_id}/intents").json()
    assert {x["status"] for x in listing} == {"awaiting_approval"}
    assert c.post(f"/clients/{client_id}/intents/{iid}/approve").json()["status"] == "approved"
    assert c.post(f"/clients/{client_id}/intents/{iid}/approve").status_code == 409
    rej = c.post(f"/clients/{client_id}/intents/{jid}/reject", json={"reason": "not now"})
    assert rej.json()["status"] == "rejected"
    with factory() as s:
        assert s.get(ActionIntent, iid).approved_by == email
    assert c.post(f"/clients/{uuid.uuid4()}/intents/{jid}/reject", json={}).status_code == 404
    assert c.post("/control/kill-switch", json={"on": True}).json() == {"kill_switch": True}
    assert c.get("/control").json()["kill_switch"] is True
    c.post("/control/kill-switch", json={"on": False})
    with factory() as s:
        assert flags.kill_switch_on(s, Settings(kill_switch=False)) is False


async def test_every_llm_call_is_logged_without_content(factory, client_id):
    def respond(req):
        return UsageLimitError("limit") if req.user_prompt == "boom" else {"ok": 1}

    prov = LoggedLLMProvider(FakeLLMProvider(respond), factory)

    def req(text):
        return LLMRequest(
            agent="t",
            system_prompt="s",
            user_prompt=text,
            output_schema={},
            client_id=str(client_id),
            correlation_id="corr-1",
        )

    await prov.generate(req("SECRET-CLIENT-TEXT"))
    with pytest.raises(UsageLimitError):
        await prov.generate(req("boom"))
    with factory() as s:
        rows = list(
            s.scalars(
                select(LLMCallLog)
                .where(LLMCallLog.client_id == client_id)
                .order_by(LLMCallLog.created_at)
            )
        )
        assert [r.exit_class for r in rows] == ["ok", "UsageLimitError"]
        assert rows[0].correlation_id == "corr-1" and len(rows[0].input_hash) == 64
        assert "SECRET-CLIENT-TEXT" not in repr([r.__dict__ for r in rows])


def test_minutes_render_from_persisted_rows_with_evidence_and_warnings(api, factory, client_id):
    c, _ = api
    rid = seed_req(factory, client_id)
    with factory() as s:
        mid = s.get(Requirement, rid).meeting_id
        m = s.get(Meeting, mid)
        m.degraded_reason = "capture stream silent for more than 20s"
        s.commit()
    md = c.get(f"/clients/{client_id}/meetings/{mid}/minutes").json()["markdown"]
    assert "Draft minutes: Kickoff" in md and "reset by email please" in md
    assert "identity linked" in md and "WARNING: capture was degraded" in md
    assert "DRAFT generated" in md  # never presented as final
    assert c.get(f"/clients/{uuid.uuid4()}/meetings/{mid}/minutes").status_code == 404


def test_correction_creates_version_and_forces_reconfirmation(api, factory, client_id):
    from app.persistence.models import RequirementVersion

    c, email = api
    rid = seed_req(factory, client_id, status="confirmed")
    r = c.post(
        f"/clients/{client_id}/requirements/{rid}/correct",
        json={
            "title": "Reset",
            "statement": "reset by email in 10 minutes",
            "reason": "attendee said 10",
        },
    )
    assert r.status_code == 200 and r.json() == {
        "id": str(rid),
        "status": "under_clarification",
        "version": 2,
    }
    with factory() as s:
        versions = list(
            s.scalars(
                select(RequirementVersion)
                .where(RequirementVersion.requirement_id == rid)
                .order_by(RequirementVersion.version)
            )
        )
        assert versions[0].statement == "reset by email" if len(versions) == 2 else True
        assert versions[-1].change_reason.startswith(f"correction by {email}")
    empty = c.post(
        f"/clients/{client_id}/requirements/{rid}/correct",
        json={"title": "x", "statement": "y", "reason": "  "},
    )
    assert empty.status_code == 422
