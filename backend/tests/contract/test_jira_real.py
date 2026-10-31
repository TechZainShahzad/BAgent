"""Opt-in, READ-ONLY checks against the real Jira sandbox. Run: pytest -m real_jira"""

import pytest

from app.config import get_settings
from app.integrations.jira.client import JiraClient
from app.integrations.jira.discovery import discover_project
from app.integrations.jira.execution import search_duplicates

pytestmark = pytest.mark.real_jira


def _client() -> JiraClient:
    s = get_settings()
    return JiraClient(s.jira_base_url, s.jira_email, s.jira_api_token.get_secret_value())


async def test_discovery_matches_expected_shape():
    c = _client()
    try:
        snap = await discover_project(c, "SCRUM", get_settings().jira_allowlist)
    finally:
        await c.aclose()
    names = {t["name"]: t for t in snap["issue_types"]}
    assert names["Epic"]["hierarchy_level"] == 1 and names["Subtask"]["subtask"]
    assert snap["permissions"]["CREATE_ISSUES"] is True


async def test_label_marker_and_summary_search_semantics():
    c = _client()
    try:
        by_label = await c.search('project = "SCRUM" AND labels = "ba-generated"')
        assert by_label, "expected at least one BA-generated issue (run the delivery demo first)"
        summary = by_label[0]["fields"]["summary"]
        found = await search_duplicates(c, "SCRUM", summary)
        assert any(i["key"] == by_label[0]["key"] for i in found)
        assert await search_duplicates(c, "SCRUM", "zzqx unrelated gibberish term") == []
    finally:
        await c.aclose()
