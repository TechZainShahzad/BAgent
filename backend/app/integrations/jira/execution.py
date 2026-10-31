"""Jira Execution: the only code that mutates Jira. No LLM.

Invariants (CLAUDE.md sections 9 and 11):
- The ActionIntent is persisted (and committed) BEFORE any write.
- Every created issue carries label `ba-intent-<intent_id>`.
- After a timeout/unknown outcome we search by that marker BEFORE any retry. Never blind-retry.
- Allowlist and kill switch are re-checked at execution time (defense in depth).
- There is no delete / permission / sprint code path.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.jira import adf
from app.integrations.jira.client import JiraClient
from app.integrations.jira.errors import (
    JiraAuthError,
    JiraError,
    JiraNotFoundError,
    JiraRateLimitedError,
    JiraUnknownOutcomeError,
    JiraValidationError,
)
from app.orchestration.audit import append_audit
from app.persistence.models import ActionIntent, JiraIssueMapping

log = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "rejected", "cancelled", "dry_run"}
EXECUTABLE = {"approved", "executing", "unknown_outcome"}
Sleep = Callable[[float], Awaitable[None]]


def marker_for(intent_id: uuid.UUID) -> str:
    return f"ba-intent-{intent_id.hex[:24]}"


class JiraExecutor:
    def __init__(
        self,
        client: JiraClient,
        allowlist: set[str],
        sleep: Sleep = asyncio.sleep,
        poll_attempts: int = 5,
        poll_delay_s: float = 1.0,
    ) -> None:
        self.client = client
        self.allowlist = allowlist
        self.sleep = sleep
        self.poll_attempts = poll_attempts
        self.poll_delay_s = poll_delay_s

    # ------------------------------------------------------------------ public
    async def execute(
        self, session: Session, intent: ActionIntent, writes_allowed: bool = True
    ) -> ActionIntent:
        if intent.status in TERMINAL:
            return intent
        if intent.project_key not in self.allowlist:
            return self._fail(session, intent, "project not in JIRA_PROJECT_ALLOWLIST")
        if not writes_allowed:
            intent.status = "held" if intent.status == "approved" else intent.status
            append_audit(
                session, "intent.held_kill_switch", {"intent_id": str(intent.id)}, intent.client_id
            )
            session.commit()
            return intent
        if intent.status not in EXECUTABLE:
            raise ValueError(f"intent {intent.id} in status {intent.status!r} is not executable")

        if intent.status in {"executing", "unknown_outcome"}:
            # A previous attempt may have applied the change. Reconcile BEFORE any retry.
            found = await self._reconcile(intent)
            if found is not None:
                return await self._finalize(session, intent, found)
        if intent.attempts >= intent.max_attempts:
            return self._fail(session, intent, "max attempts reached without confirmed outcome")

        intent.attempts += 1
        intent.status = "executing"
        intent.last_error = None
        append_audit(
            session,
            "intent.executing",
            {"intent_id": str(intent.id), "kind": intent.kind, "attempt": intent.attempts},
            intent.client_id,
        )
        session.commit()  # persisted BEFORE the side effect

        try:
            result = await self._perform(session, intent)
        except JiraUnknownOutcomeError as exc:
            intent.status = "unknown_outcome"
            intent.last_error = str(exc)[:500]
            append_audit(
                session, "intent.unknown_outcome", {"intent_id": str(intent.id)}, intent.client_id
            )
            session.commit()
            found = await self._reconcile(intent)
            if found is not None:
                return await self._finalize(session, intent, found)
            raise  # confirmed-absent or still unknown: job retry; next run reconciles first
        except JiraRateLimitedError:
            intent.status = "approved"  # 429 means nothing was applied; safe to retry later
            session.commit()
            raise
        except (JiraValidationError, JiraAuthError, JiraNotFoundError) as exc:
            return self._fail(session, intent, f"{type(exc).__name__}: {exc}")
        return await self._finalize(session, intent, result)

    # ------------------------------------------------------------------ actions
    async def _perform(self, session: Session, intent: ActionIntent) -> dict[str, Any]:
        p = intent.payload
        if intent.kind == "create_issue":
            fields: dict[str, Any] = {
                "project": {"key": intent.project_key},
                "issuetype": {"id": p["issue_type_id"]},
                "summary": p["summary"],
                "description": p["description_adf"],
                "labels": [intent.marker, *p.get("labels", [])],
            }
            if p.get("parent_key"):
                fields["parent"] = {"key": p["parent_key"]}
            created = await self.client.create_issue(fields)
            return {"key": created["key"], "id": str(created["id"])}
        if intent.kind == "update_issue":
            current = await self.client.get_issue(p["jira_key"])
            cf = current.get("fields", {})
            if intent.prior_snapshot is None:  # prior-version record kept locally
                intent.prior_snapshot = {
                    "summary": cf.get("summary"),
                    "description_text": adf.to_text(cf.get("description")),
                    "description_adf": cf.get("description"),
                }
                session.commit()
            body: dict[str, Any] = {"update": {"labels": [{"add": intent.marker}]}}
            fields_update: dict[str, Any] = {}
            if p.get("summary"):
                fields_update["summary"] = p["summary"]
            if p.get("description_adf"):
                fields_update["description"] = p["description_adf"]
            if fields_update:
                body["fields"] = fields_update
            await self.client.update_issue(p["jira_key"], body)
            return {"key": p["jira_key"], "id": str(current.get("id", ""))}
        if intent.kind == "add_reference":
            await self.client.add_comment(p["jira_key"], p["comment_adf"])
            return {"key": p["jira_key"], "id": ""}
        raise ValueError(f"unknown intent kind {intent.kind!r}")

    async def _reconcile(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Look for evidence that the action was applied. Bounded wait for search lag."""
        for i in range(self.poll_attempts):
            found = await self._probe(intent)
            if found is not None:
                return found
            if i + 1 < self.poll_attempts:
                await self.sleep(self.poll_delay_s * (i + 1))
        return None

    async def _probe(self, intent: ActionIntent) -> dict[str, Any] | None:
        if intent.kind == "create_issue":
            jql = f'project = "{intent.project_key}" AND labels = "{intent.marker}"'
            issues = await self.client.search(jql, ["summary", "labels"], 5)
            if issues:
                return {"key": issues[0]["key"], "id": str(issues[0]["id"])}
            return None
        key = intent.payload["jira_key"]
        if intent.kind == "update_issue":
            issue = await self.client.get_issue(key, "summary,description,labels")
            if intent.marker in issue.get("fields", {}).get("labels", []):
                return {"key": key, "id": str(issue.get("id", ""))}
            return None
        for c in await self.client.get_comments(key):
            if intent.marker in adf.to_text(c.get("body")):
                return {"key": key, "id": ""}
        return None

    # ------------------------------------------------------------------ outcomes
    async def _finalize(
        self, session: Session, intent: ActionIntent, result: dict[str, Any]
    ) -> ActionIntent:
        intent.jira_key = result["key"]
        intent.jira_id = result.get("id") or intent.jira_id
        intent.status = "succeeded"
        intent.last_error = None
        if intent.requirement_id is not None:
            existing = session.scalars(
                select(JiraIssueMapping).where(
                    JiraIssueMapping.client_id == intent.client_id,
                    JiraIssueMapping.jira_key == intent.jira_key,
                )
            ).first()
            if existing is None:
                session.add(
                    JiraIssueMapping(
                        client_id=intent.client_id,
                        requirement_id=intent.requirement_id,
                        intent_id=intent.id,
                        project_key=intent.project_key,
                        jira_key=intent.jira_key,
                        jira_id=intent.jira_id or "",
                        relation="reference" if intent.kind == "add_reference" else "primary",
                    )
                )
        append_audit(
            session,
            "intent.succeeded",
            {"intent_id": str(intent.id), "kind": intent.kind, "jira_key": intent.jira_key},
            intent.client_id,
        )
        session.commit()  # mapping + outcome persisted immediately after the side effect
        await self._evidence_comment(session, intent)
        return intent

    async def _evidence_comment(self, session: Session, intent: ActionIntent) -> None:
        """Transparency: link evidence on the Jira issue itself (idempotent via marker text)."""
        body = intent.payload.get("evidence_comment_adf")
        if not body or intent.kind == "add_reference" or not intent.jira_key:
            return
        tag = f"{intent.marker}:evidence"
        try:
            for c in await self.client.get_comments(intent.jira_key):
                if tag in adf.to_text(c.get("body")):
                    return
            await self.client.add_comment(intent.jira_key, body)
        except JiraError as exc:
            append_audit(
                session,
                "intent.comment_failed",
                {"intent_id": str(intent.id), "error": str(exc)[:300]},
                intent.client_id,
            )
            session.commit()

    @staticmethod
    def _fail(session: Session, intent: ActionIntent, reason: str) -> ActionIntent:
        intent.status = "failed"
        intent.last_error = reason[:1000]
        append_audit(
            session,
            "intent.failed",
            {"intent_id": str(intent.id), "reason": reason[:300]},
            intent.client_id,
        )
        session.commit()
        return intent


def _jql_text(summary: str) -> str:
    words = [
        w
        for w in "".join(c if c.isalnum() or c == " " else " " for c in summary).split()
        if len(w) > 2
    ]
    return " ".join(words[:8])


async def search_duplicates(
    client: JiraClient, project_key: str, summary: str
) -> list[dict[str, Any]]:
    """Existing issues in the project whose summary resembles `summary` (read-only)."""
    text = _jql_text(summary).replace('"', "")
    if not text:
        return []
    jql = f'project = "{project_key}" AND summary ~ "{text}" ORDER BY created DESC'
    return await client.search(jql, ["summary", "labels", "status"], 10)


def build_executor(settings: Any) -> JiraExecutor:
    client = JiraClient(
        settings.jira_base_url, settings.jira_email, settings.jira_api_token.get_secret_value()
    )
    return JiraExecutor(client, settings.jira_allowlist)
