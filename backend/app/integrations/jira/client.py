"""Jira Cloud REST v3 client. The ONLY module that talks HTTP to Jira.

By design there is no delete, permission-change or sprint/release method: those operations have
no code path (CLAUDE.md section 9). Write failures with unknown outcome raise
JiraUnknownOutcomeError so callers reconcile by marker before any retry.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.integrations.jira.errors import (
    JiraAuthError,
    JiraError,
    JiraNotFoundError,
    JiraRateLimitedError,
    JiraTransientError,
    JiraUnknownOutcomeError,
    JiraValidationError,
)

log = logging.getLogger(__name__)
MAX_READ_RETRIES = 3


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not email or not api_token:
            raise JiraAuthError("JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN are required")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=(email, api_token),  # Basic auth, not Bearer
            timeout=timeout_s,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        write = method.upper() in {"POST", "PUT"} and not path.endswith("/search/jql")
        attempts = 1 if write else MAX_READ_RETRIES
        for attempt in range(attempts):
            try:
                resp = await self._http.request(method, path, json=json, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if write:
                    raise JiraUnknownOutcomeError(f"{method} {path}: {type(exc).__name__}") from exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise JiraTransientError(f"{method} {path}: {type(exc).__name__}") from exc
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "2"))
                if not write and attempt + 1 < attempts:
                    await asyncio.sleep(min(retry_after, 10.0))
                    continue
                raise JiraRateLimitedError(retry_after)
            if resp.status_code >= 500:
                if write:
                    raise JiraUnknownOutcomeError(f"{method} {path}: HTTP {resp.status_code}")
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise JiraTransientError(f"{method} {path}: HTTP {resp.status_code}")
            return self._finish(resp, method, path)
        raise JiraTransientError(f"{method} {path}: exhausted retries")  # pragma: no cover

    @staticmethod
    def _finish(resp: httpx.Response, method: str, path: str) -> Any:
        code = resp.status_code
        if code in (401, 403):
            raise JiraAuthError(f"{method} {path}: HTTP {code}")
        if code == 404:
            raise JiraNotFoundError(f"{method} {path}: not found")
        if 400 <= code < 500:
            raise JiraValidationError(f"{method} {path}: HTTP {code}: {resp.text[:400]}")
        if code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise JiraError(f"{method} {path}: non-JSON response") from exc

    # ---- reads ----
    async def myself(self) -> dict[str, Any]:
        return await self._request("GET", "/rest/api/3/myself")

    async def get_project(self, key: str) -> dict[str, Any]:
        return await self._request("GET", f"/rest/api/3/project/{key}")

    async def project_statuses(self, key: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/rest/api/3/project/{key}/statuses")

    async def issue_type_fields(self, key: str, issue_type_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/rest/api/3/issue/createmeta/{key}/issuetypes/{issue_type_id}"
        )

    async def my_permissions(self, key: str, permissions: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/rest/api/3/mypermissions",
            params={"projectKey": key, "permissions": permissions},
        )

    async def my_permissions_global(self, permissions: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/rest/api/3/mypermissions", params={"permissions": permissions}
        )

    async def search(
        self, jql: str, fields: list[str] | None = None, max_results: int = 50
    ) -> list[dict[str, Any]]:
        body = {"jql": jql, "fields": fields or ["summary", "labels"], "maxResults": max_results}
        data = await self._request("POST", "/rest/api/3/search/jql", json=body)
        return list(data.get("issues", []))

    async def get_issue(
        self, key: str, fields: str = "summary,description,labels"
    ) -> dict[str, Any]:
        return await self._request("GET", f"/rest/api/3/issue/{key}", params={"fields": fields})

    async def get_comments(self, key: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/rest/api/3/issue/{key}/comment")
        return list(data.get("comments", []))

    # ---- writes (create / update / comment only) ----
    async def create_issue(self, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/rest/api/3/issue", json={"fields": fields})

    async def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a Jira project (used only by provision.py; there is no delete counterpart)."""
        return await self._request("POST", "/rest/api/3/project", json=body)

    async def update_issue(self, key: str, body: dict[str, Any]) -> None:
        await self._request("PUT", f"/rest/api/3/issue/{key}", json=body)

    async def add_comment(self, key: str, adf_body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/rest/api/3/issue/{key}/comment", json={"body": adf_body}
        )
