"""Which Jira projects the platform may touch: the configured allowlist PLUS projects the
platform itself created (they are registered in our database, so they are known and ours)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.jira.errors import JiraError
from app.integrations.jira.provision import provision_project, suggest_key
from app.orchestration.audit import append_audit
from app.persistence.models import Project
from app.services import clients, flags


def provisioned_keys(session: Session) -> set[str]:
    return {
        k
        for k in session.scalars(
            select(Project.jira_project_key).where(
                Project.jira_provisioned.is_(True), Project.jira_project_key.is_not(None)
            )
        )
        if k
    }


def effective_allowlist(session: Session, settings: Settings) -> set[str]:
    return set(settings.jira_allowlist) | provisioned_keys(session)


class ProvisionRefused(ValueError):
    """Provisioning was refused up front (kill switch, Jira not configured, bad input)."""


async def provision_for_client(
    session: Session,
    settings: Settings,
    jira: Any,
    client_id: uuid.UUID,
    name: str,
    jira_key: str | None,
    create_in_jira: bool,
) -> Project:
    """Register a platform project; optionally create the matching Jira project first.

    The request is audited and committed BEFORE the Jira write, and the outcome after it.
    `jira` is a JiraClient (only used when create_in_jira is set).
    """
    if not name.strip():
        raise ProvisionRefused("project name is required")
    if not create_in_jira:
        return clients.create_project(
            session, client_id, name, jira_key or None, effective_allowlist(session, settings)
        )
    if flags.kill_switch_on(session, settings):
        raise ProvisionRefused(
            "kill switch is on: Jira writes (including project creation) are held"
        )
    if not (settings.jira_base_url and settings.jira_email):
        raise ProvisionRefused(
            "Jira is not configured (JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)"
        )
    key = (jira_key or suggest_key(name)).strip().upper()
    append_audit(session, "project.jira_provision_requested", {"name": name, "key": key}, client_id)
    session.commit()
    try:
        created = await provision_project(jira, key, name)
    except JiraError as exc:
        append_audit(
            session,
            "project.jira_provision_failed",
            {"key": key, "error": f"{type(exc).__name__}: {exc}"},
            client_id,
        )
        session.commit()
        raise
    project = clients.create_project(session, client_id, name, created["key"], None)
    project.jira_provisioned = True
    append_audit(
        session,
        "project.jira_provisioned",
        {"project_id": str(project.id), "key": created["key"], "jira_id": created["id"]},
        client_id,
    )
    return project
