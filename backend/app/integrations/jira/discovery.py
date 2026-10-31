"""Per-project discovery: key, issue types, hierarchy, required fields, statuses, permissions."""

from datetime import UTC, datetime
from typing import Any

from app.integrations.jira.client import JiraClient
from app.integrations.jira.errors import JiraProjectNotAllowedError

PERMISSIONS = "CREATE_ISSUES,EDIT_ISSUES,ADD_COMMENTS,TRANSITION_ISSUES"


async def discover_project(
    client: JiraClient, project_key: str, allowlist: set[str]
) -> dict[str, Any]:
    if project_key not in allowlist:
        raise JiraProjectNotAllowedError(f"{project_key} is not in JIRA_PROJECT_ALLOWLIST")
    project = await client.get_project(project_key)
    types: list[dict[str, Any]] = []
    for it in project.get("issueTypes", []):
        meta = await client.issue_type_fields(project_key, str(it["id"]))
        fields = meta.get("fields", meta.get("values", []))
        types.append(
            {
                "id": str(it["id"]),
                "name": it["name"],
                "subtask": bool(it.get("subtask")),
                "hierarchy_level": it.get("hierarchyLevel"),
                "required_fields": [f["key"] for f in fields if f.get("required")],
                "field_keys": [f["key"] for f in fields],
                "allowed_values": {
                    f["key"]: [v.get("name") or v.get("value") for v in f["allowedValues"]]
                    for f in fields
                    if f.get("allowedValues")
                },
            }
        )
    statuses = await client.project_statuses(project_key)
    perms = await client.my_permissions(project_key, PERMISSIONS)
    return {
        "key": project["key"],
        "name": project.get("name"),
        "style": project.get("style"),
        "issue_types": types,
        "statuses": {s["name"]: [x["name"] for x in s["statuses"]] for s in statuses},
        "permissions": {
            k: bool(v.get("havePermission")) for k, v in perms.get("permissions", {}).items()
        },
        "fetched_at": datetime.now(UTC).isoformat(),
    }
