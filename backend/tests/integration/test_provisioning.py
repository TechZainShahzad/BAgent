import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.main import create_app
from app.config import Settings
from app.integrations.jira.client import JiraClient
from app.integrations.jira.provision import suggest_key
from app.persistence.models import Project
from app.services.auth import create_user
from tests.support.fake_jira import FakeJira

PW = "a-long-test-password"


@pytest.fixture
def env(factory):
    fake = FakeJira()
    settings = Settings(
        jira_project_allowlist="SCRUM", jira_base_url="https://x.atlassian.net", jira_email="a@b.c"
    )
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, PW)
        s.commit()
    app = create_app(
        settings,
        factory,
        jira_client_factory=lambda: JiraClient(
            "https://x.atlassian.net", "a@b.c", "t", transport=fake.transport()
        ),
    )
    c = TestClient(app)
    csrf = c.post("/auth/login", json={"email": email, "password": PW}).json()["csrf_token"]
    c.headers.update({"X-CSRF-Token": csrf})
    yield c, fake
    with factory() as s:  # the test database is shared: leave no provisioned project behind
        s.execute(delete(Project).where(Project.jira_provisioned.is_(True)))
        s.commit()


def test_suggest_key():
    assert suggest_key("School Portal") == "SP"
    assert suggest_key("9 lives") == "P9L"


def test_provision_creates_jira_project_registers_it_and_allows_it(env, client_id):
    c, fake = env
    r = c.post(
        f"/clients/{client_id}/projects/provision",
        json={"name": "Hospital App", "jira_key": "HOSP"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["jira_project_key"] == "HOSP" and r.json()["jira_provisioned"] is True
    assert fake.created_projects[0]["projectTemplateKey"].endswith("agility-scrum")
    assert "HOSP" in c.get("/control").json()["jira_allowlist"]
    assert [p["jira_project_key"] for p in c.get(f"/clients/{client_id}/projects").json()] == [
        "HOSP"
    ]


def test_provision_refuses_existing_key_and_no_write_happens(env, client_id):
    c, fake = env
    r = c.post(
        f"/clients/{client_id}/projects/provision", json={"name": "Dup", "jira_key": "SCRUM"}
    )
    assert r.status_code == 422 and "already exists" in r.json()["detail"]
    assert fake.created_projects == []


def test_provision_refuses_without_admin_permission(env, client_id):
    c, fake = env
    fake.can_administer = False
    r = c.post(
        f"/clients/{client_id}/projects/provision", json={"name": "Nope", "jira_key": "NOPE"}
    )
    assert r.status_code == 422 and "Administer" in r.json()["detail"]
    assert fake.created_projects == []
    assert c.get("/jira/capabilities").json()["can_create_projects"] is False


def test_provision_rejects_bad_key(env, client_id):
    c, _ = env
    r = c.post(f"/clients/{client_id}/projects/provision", json={"name": "Bad", "jira_key": "1x"})
    assert r.status_code == 422


def test_local_only_project_without_jira(env, client_id):
    c, fake = env
    r = c.post(
        f"/clients/{client_id}/projects/provision", json={"name": "Local", "create_in_jira": False}
    )
    assert r.status_code == 201 and r.json()["jira_project_key"] is None
    assert fake.created_projects == []


def test_overview_and_clients_list_are_cross_client(env, client_id):
    c, _ = env
    ov = c.get("/overview").json()
    assert ov["totals"]["clients"] >= 1 and "requirements_by_status" in ov
    assert any(row["id"] == str(client_id) for row in c.get("/clients").json())
