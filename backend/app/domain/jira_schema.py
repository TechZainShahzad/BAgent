"""Pure validation of issue drafts against a discovered Jira project snapshot."""

from dataclasses import dataclass
from typing import Any

# Fields the platform supplies itself or Jira fills automatically.
_AUTO_FIELDS = {"project", "issuetype", "summary", "reporter"}


@dataclass(frozen=True)
class IssueTypeInfo:
    id: str
    name: str
    subtask: bool
    hierarchy_level: int | None
    required_fields: tuple[str, ...]
    field_keys: tuple[str, ...]


class SchemaView:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.project_key: str = snapshot["key"]
        self.types: dict[str, IssueTypeInfo] = {}
        for t in snapshot.get("issue_types", []):
            self.types[t["name"].lower()] = IssueTypeInfo(
                id=str(t["id"]),
                name=t["name"],
                subtask=bool(t.get("subtask")),
                hierarchy_level=t.get("hierarchy_level"),
                required_fields=tuple(t.get("required_fields", [])),
                field_keys=tuple(t.get("field_keys", [])),
            )

    def type(self, name: str) -> IssueTypeInfo | None:
        return self.types.get(name.lower())

    def can_create(self, name: str) -> bool:
        t = self.type(name)
        return t is not None and not t.subtask

    def epic_type(self) -> IssueTypeInfo | None:
        for t in self.types.values():
            if t.hierarchy_level == 1:
                return t
        return None

    def validate_draft(
        self, issue_type: str, summary: str, parent_key: str | None, extra_fields: set[str]
    ) -> list[str]:
        """Return a list of problems; empty means the draft is valid for this project."""
        problems: list[str] = []
        t = self.type(issue_type)
        if t is None:
            return [f"issue type {issue_type!r} does not exist in project {self.project_key}"]
        if t.subtask:
            problems.append("sub-tasks are not created by the platform")
        if not summary.strip():
            problems.append("summary is empty")
        if len(summary) > 255:
            problems.append("summary exceeds 255 characters")
        provided = _AUTO_FIELDS | extra_fields
        for f in t.required_fields:
            if f not in provided:
                problems.append(f"required field {f!r} is not provided")
        if parent_key:
            if "parent" not in t.field_keys:
                problems.append(f"issue type {t.name!r} does not support a parent")
            if t.hierarchy_level == 1:
                problems.append("an epic cannot have a parent")
        for f in extra_fields:
            if f not in t.field_keys and f not in _AUTO_FIELDS:
                problems.append(f"field {f!r} is not available for {t.name!r}")
        return problems
