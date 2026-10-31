"""HTTP client the capture companion uses to talk to the LOCAL backend API (127.0.0.1).

Authenticated session + CSRF like the dashboard. Failures are raised, never swallowed.
"""

from typing import Any

import httpx


class CaptureApiError(Exception):
    pass


class CaptureClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport)
        self._csrf = ""

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"X-CSRF-Token": self._csrf} if self._csrf else {}
        try:
            r = self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise CaptureApiError(f"{method} {path}: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except ValueError:
                detail = r.text[:200]
            raise CaptureApiError(f"{method} {path}: HTTP {r.status_code} {detail}")
        return r.json()

    def use_session(self, cookie: str, csrf: str) -> None:
        """Reuse an existing dashboard session (the dashboard hands it to the capture process)."""
        self._http.cookies.set("ba_session", cookie)
        self._csrf = csrf

    def login(self, email: str, password: str) -> None:
        self._csrf = self._call("POST", "/auth/login", json={"email": email, "password": password})[
            "csrf_token"
        ]

    def start(
        self,
        client_id: str,
        title: str,
        consent_basis: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        r = self._call(
            "POST",
            f"/clients/{client_id}/live-meetings",
            json={"title": title, "consent_basis": consent_basis, **(extra or {})},
        )
        return str(r["id"])

    def next_utterance(self, client_id: str, meeting_id: str) -> dict[str, Any]:
        return dict(
            self._call("GET", f"/clients/{client_id}/live-meetings/{meeting_id}/next-utterance")
        )

    def agent_said(
        self, client_id: str, meeting_id: str, text: str, action: str, topic: str
    ) -> None:
        self._call(
            "POST",
            f"/clients/{client_id}/live-meetings/{meeting_id}/agent-said",
            json={"text": text, "action": action, "topic": topic},
        )

    def state(self, client_id: str, meeting_id: str) -> dict[str, Any]:
        return dict(self._call("GET", f"/clients/{client_id}/live-meetings/{meeting_id}/state"))

    def segments(self, client_id: str, meeting_id: str, segs: list[dict[str, Any]]) -> int:
        r = self._call(
            "POST",
            f"/clients/{client_id}/live-meetings/{meeting_id}/segments",
            json={"segments": segs},
        )
        return int(r["accepted"])

    def heartbeat(self, client_id: str, meeting_id: str) -> str:
        return str(
            self._call("POST", f"/clients/{client_id}/live-meetings/{meeting_id}/heartbeat")[
                "status"
            ]
        )

    def suggestion(self, client_id: str, meeting_id: str) -> dict[str, Any]:
        return dict(
            self._call("GET", f"/clients/{client_id}/live-meetings/{meeting_id}/suggestion")
        )

    def end(self, client_id: str, meeting_id: str) -> dict[str, Any]:
        return dict(self._call("POST", f"/clients/{client_id}/live-meetings/{meeting_id}/end"))
