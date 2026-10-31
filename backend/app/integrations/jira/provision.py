"""Create a Jira project for a platform project (the only place that does).

Creates a team-managed Scrum software project (it has a backlog, like SCRUM). Refuses to touch an
existing project, checks the token's admin permission first, and never deletes anything.
"""

import re
from typing import Any

from app.integrations.jira.client import JiraClient
from app.integrations.jira.errors import JiraError, JiraNotFoundError, JiraUnknownOutcomeError

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
SCRUM_TEMPLATE = "com.pyxis.greenhopper.jira:gh-simplified-agility-scrum"


class JiraProvisionError(JiraError):
    """The project cannot be created (bad key, already exists, no permission...)."""


def suggest_key(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    key = "".join(w[0] for w in words)[:6].upper() or "PRJ"
    if not key[0].isalpha():
        key = "P" + key
    return key.ljust(2, "X")[:10]


async def can_create_projects(client: JiraClient) -> tuple[bool, str]:
    """Read-only: does the API token's user hold the global 'Administer Jira' permission?"""
    try:
        data = await client.my_permissions_global("ADMINISTER")
    except JiraError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    ok = bool(data.get("permissions", {}).get("ADMINISTER", {}).get("havePermission"))
    return ok, "" if ok else "the API token's user lacks the 'Administer Jira' permission"


async def provision_project(client: JiraClient, key: str, name: str) -> dict[str, Any]:
    key = key.strip().upper()
    if not KEY_RE.match(key):
        raise JiraProvisionError(
            "Jira key must be 2-10 characters: letters and digits, starting with a letter"
        )
    if not name.strip():
        raise JiraProvisionError("project name is required")
    try:
        await client.get_project(key)
    except JiraNotFoundError:
        pass  # good: the key is free
    else:
        raise JiraProvisionError(f"a Jira project with key {key} already exists; choose another")
    ok, why = await can_create_projects(client)
    if not ok:
        raise JiraProvisionError(f"cannot create projects: {why}")
    me = await client.myself()
    body = {
        "key": key,
        "name": name.strip()[:80],
        "projectTypeKey": "software",
        "projectTemplateKey": SCRUM_TEMPLATE,
        "leadAccountId": me["accountId"],
        "assigneeType": "PROJECT_LEAD",
        "description": "Created by the BA platform",
    }
    try:
        created = await client.create_project(body)
    except JiraUnknownOutcomeError:
        # the key was free a moment ago: if it exists now, our create went through
        await client.get_project(key)
        created = {"key": key, "id": ""}
    return {"key": created.get("key", key), "id": str(created.get("id", ""))}
