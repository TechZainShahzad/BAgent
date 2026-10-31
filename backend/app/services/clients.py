import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orchestration.audit import append_audit
from app.persistence.models import Client, Project


def create_client(session: Session, name: str) -> Client:
    client = Client(name=name.strip())
    session.add(client)
    session.flush()
    append_audit(
        session, "client.created", {"client_id": str(client.id), "name": client.name}, client.id
    )
    return client


def list_clients(session: Session) -> list[Client]:
    return list(session.scalars(select(Client).order_by(Client.name)))


def create_project(
    session: Session,
    client_id: uuid.UUID,
    name: str,
    jira_project_key: str | None = None,
    allowlist: set[str] | None = None,
) -> Project:
    if jira_project_key and allowlist is not None and jira_project_key not in allowlist:
        raise ValueError(f"Jira project {jira_project_key!r} is not in JIRA_PROJECT_ALLOWLIST")
    project = Project(client_id=client_id, name=name.strip(), jira_project_key=jira_project_key)
    session.add(project)
    session.flush()
    append_audit(
        session,
        "project.created",
        {"project_id": str(project.id), "name": project.name, "jira": jira_project_key},
        client_id,
    )
    return project


def list_projects(session: Session, client_id: uuid.UUID) -> list[Project]:
    return list(session.scalars(select(Project).where(Project.client_id == client_id)))
