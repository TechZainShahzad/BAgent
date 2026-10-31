import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.config import Settings
from app.persistence.models import Job, Meeting, TranscriptSegment
from app.services.auth import create_user

PW = "a-long-test-password"
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Priya>We need an audit log for every login.\n"


@pytest.fixture
def api(factory):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    c = TestClient(create_app(Settings(jira_project_allowlist="SCRUM"), factory))
    csrf = c.post("/auth/login", json={"email": email, "password": PW}).json()["csrf_token"]
    c.headers.update({"X-CSRF-Token": csrf})
    return c


def test_projects_create_list_and_allowlist_is_enforced(api, client_id):
    ok = api.post(
        f"/clients/{client_id}/projects", json={"name": "Portal", "jira_project_key": "SCRUM"}
    )
    assert ok.status_code == 201 and ok.json()["jira_project_key"] == "SCRUM"
    bad = api.post(
        f"/clients/{client_id}/projects", json={"name": "X", "jira_project_key": "OTHER"}
    )
    assert bad.status_code == 422 and "ALLOWLIST" in bad.json()["detail"]
    assert [p["name"] for p in api.get(f"/clients/{client_id}/projects").json()] == ["Portal"]


def test_stakeholders_listed_per_client_with_verification_state(api, client_id):
    api.post(
        f"/clients/{client_id}/stakeholders",
        json={"name": "Priya", "has_decision_authority": True, "identity_verified": True},
    )
    rows = api.get(f"/clients/{client_id}/stakeholders").json()
    assert rows[0]["name"] == "Priya" and rows[0]["identity_verified"] is True
    assert api.get(f"/clients/{uuid.uuid4()}/stakeholders").json() == []  # no cross-client view


def test_control_exposes_jira_link_info_not_secrets(api):
    c = api.get("/control").json()
    assert c["jira_allowlist"] == ["SCRUM"] and "jira_base_url" in c
    assert "token" not in str(c).lower()


def test_upload_transcript_requires_consent_and_queues_analysis(api, factory, client_id):
    files = {"file": ("kickoff.vtt", io.BytesIO(VTT.encode()), "text/vtt")}
    refused = api.post(
        f"/clients/{client_id}/meetings/import",
        files=files,
        data={"title": "Kickoff", "consent_basis": "  "},
    )
    assert refused.status_code == 412
    files = {"file": ("kickoff.vtt", io.BytesIO(VTT.encode()), "text/vtt")}
    ok = api.post(
        f"/clients/{client_id}/meetings/import",
        files=files,
        data={"title": "Kickoff", "consent_basis": "attested by operator"},
    )
    assert ok.status_code == 201
    mid = uuid.UUID(ok.json()["id"])
    with factory() as s:
        assert s.get(Meeting, mid).status == "queued"
        assert (
            len(
                s.scalars(
                    select(TranscriptSegment).where(TranscriptSegment.meeting_id == mid)
                ).all()
            )
            == 1
        )
        assert s.scalars(
            select(Job).where(Job.client_id == client_id, Job.kind == "meeting.analyze")
        ).one()


def test_upload_rejects_bad_empty_and_huge_files_visibly(api, client_id):
    def up(name, data):
        return api.post(
            f"/clients/{client_id}/meetings/import",
            files={"file": (name, io.BytesIO(data), "application/octet-stream")},
            data={"title": "T", "consent_basis": "attested"},
        )

    assert up("a.pdf", b"x").status_code == 422
    assert up("a.vtt", b"").status_code == 422
    assert up("a.vtt", b"WEBVTT\n\nNOTE nothing\n").status_code == 422
    assert up("a.vtt", b"x" * (5 * 1024 * 1024 + 10)).status_code == 413


def test_upload_needs_auth_and_csrf(factory, client_id):
    anon = TestClient(create_app(Settings(), factory))
    r = anon.post(
        f"/clients/{client_id}/meetings/import",
        files={"file": ("a.vtt", io.BytesIO(VTT.encode()), "text/vtt")},
        data={"title": "T", "consent_basis": "x"},
    )
    assert r.status_code == 401
