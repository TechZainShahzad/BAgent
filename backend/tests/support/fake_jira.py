"""In-memory Jira Cloud double (httpx.MockTransport) with fault injection."""

import json
import re
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[3] / "docs" / "spikes" / "fixtures"


class FakeJira:
    def __init__(self) -> None:
        self.issues: dict[str, dict[str, Any]] = {}
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self._n = 100
        self.calls: list[tuple[str, str]] = []
        # fault injection
        self.create_faults: list[
            str
        ] = []  # "timeout_after_apply" | "timeout_before_apply" | "500_after_apply" | "400"
        self.search_empty_polls = 0  # number of search calls that return nothing (index lag)
        self.rate_limit_next = 0
        self.fail_comments = False
        self.update_faults: list[str] = []
        self.projects: set[str] = {"SCRUM"}
        self.can_administer = True
        self.created_projects: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ---- helpers
    @property
    def write_calls(self) -> list[tuple[str, str]]:
        return [
            c for c in self.calls if c[0] in {"POST", "PUT", "DELETE"} and "/search/" not in c[1]
        ]

    def _json(
        self, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    def _new_issue(self, fields: dict[str, Any]) -> dict[str, Any]:
        self._n += 1
        key = f"SCRUM-{self._n}"
        issue = {"id": str(10000 + self._n), "key": key, "fields": json.loads(json.dumps(fields))}
        self.issues[key] = issue
        self.comments[key] = []
        return issue

    # ---- router
    def _handle(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        self.calls.append((method, path))
        if self.rate_limit_next > 0:
            self.rate_limit_next -= 1
            return self._json(429, {"message": "rate"}, {"Retry-After": "0"})
        body = json.loads(req.content) if req.content else None

        if path.endswith("/myself"):
            return self._json(200, {"accountId": "a", "displayName": "Tester"})
        if path == "/rest/api/3/project" and method == "POST":
            assert body is not None
            if body["key"] in self.projects:
                return self._json(400, {"errors": {"projectKey": "exists"}})
            self.projects.add(body["key"])
            self.created_projects.append(body)
            return self._json(201, {"id": "20001", "key": body["key"]})
        m = re.fullmatch(r"/rest/api/3/project/([A-Z][A-Z0-9]*)", path)
        if m:
            if m.group(1) not in self.projects:
                return self._json(404, {"errorMessages": ["No project could be found"]})
            text = (FIXTURES / "project_SCRUM.json").read_text("utf-8")
            return self._json(200, json.loads(text.replace("SCRUM", m.group(1))))
        if path.endswith("/statuses"):
            return self._json(
                200, [{"name": "Story", "statuses": [{"name": "To Do"}, {"name": "Done"}]}]
            )
        if "/createmeta/" in path:
            meta = json.loads((FIXTURES / "createmeta_story_fields.json").read_text("utf-8"))
            return self._json(200, meta)
        if path.endswith("/mypermissions") and "ADMINISTER" in str(req.url.query):
            return self._json(
                200, {"permissions": {"ADMINISTER": {"havePermission": self.can_administer}}}
            )
        if path.endswith("/mypermissions"):
            return self._json(200, {"permissions": {"CREATE_ISSUES": {"havePermission": True}}})
        if path.endswith("/search/jql") and method == "POST":
            return self._search(body)
        m = re.fullmatch(r"/rest/api/3/issue/([A-Z]+-\d+)/comment", path)
        if m:
            key = m.group(1)
            if method == "GET":
                return self._json(200, {"comments": self.comments.get(key, [])})
            if self.fail_comments:
                return self._json(400, {"errors": {"comment": "nope"}})
            self.comments[key].append(
                {"id": str(len(self.comments[key]) + 1), "body": body["body"]}
            )
            return self._json(201, {"id": "1"})
        m = re.fullmatch(r"/rest/api/3/issue/([A-Z]+-\d+)", path)
        if m:
            key = m.group(1)
            if key not in self.issues:
                return self._json(404, {})
            if method == "GET":
                return self._json(200, self.issues[key])
            if method == "PUT":
                return self._update(key, body)
            if method == "DELETE":
                raise AssertionError("DELETE must never be called")
        if path == "/rest/api/3/issue" and method == "POST":
            return self._create(body)
        return self._json(404, {"unhandled": path})

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        fault = self.create_faults.pop(0) if self.create_faults else None
        if fault == "400":
            return self._json(400, {"errors": {"summary": "bad"}})
        if fault == "timeout_before_apply":
            raise httpx.ReadTimeout("dropped before apply")
        issue = self._new_issue(body["fields"])
        if fault == "timeout_after_apply":
            raise httpx.ReadTimeout("applied but response lost")
        if fault == "500_after_apply":
            return self._json(500, {"message": "boom"})
        return self._json(201, {"id": issue["id"], "key": issue["key"]})

    def _update(self, key: str, body: dict[str, Any]) -> httpx.Response:
        fault = self.update_faults.pop(0) if self.update_faults else None
        if fault == "timeout_before_apply":
            raise httpx.ReadTimeout("dropped")
        fields = self.issues[key]["fields"]
        fields.update(body.get("fields", {}))
        for op in body.get("update", {}).get("labels", []):
            if "add" in op:
                fields.setdefault("labels", []).append(op["add"])
        if fault == "timeout_after_apply":
            raise httpx.ReadTimeout("applied, response lost")
        return httpx.Response(204)

    def _search(self, body: dict[str, Any]) -> httpx.Response:
        if self.search_empty_polls > 0:
            self.search_empty_polls -= 1
            return self._json(200, {"issues": []})
        jql = body["jql"]
        out = list(self.issues.values())
        label = re.search(r'labels = "([^"]+)"', jql)
        if label:
            out = [i for i in out if label.group(1) in i["fields"].get("labels", [])]
        summ = re.search(r'summary ~ "([^"]*)"', jql)
        if summ:
            words = summ.group(1).lower().split()
            out = [
                i
                for i in out
                if all(w in str(i["fields"].get("summary", "")).lower().split() for w in words)
            ]
        return self._json(200, {"issues": out[: body.get("maxResults", 50)]})
