import uuid

import pytest
from sqlalchemy import select

from app.integrations.jira import adf
from app.integrations.jira.client import JiraClient
from app.integrations.jira.errors import JiraRateLimitedError, JiraUnknownOutcomeError
from app.integrations.jira.execution import JiraExecutor, marker_for
from app.persistence.models import ActionIntent, JiraIssueMapping, Requirement
from tests.support.fake_jira import FakeJira


async def no_sleep(_):
    return None


def make(fake: FakeJira):
    client = JiraClient("https://x.atlassian.net", "a@b.c", "tok", transport=fake.transport())
    return JiraExecutor(client, {"SCRUM"}, sleep=no_sleep, poll_attempts=3)


def new_intent(
    factory, client_id, kind="create_issue", payload=None, project_key="SCRUM", status="approved"
):
    with factory() as s:
        req = Requirement(
            client_id=client_id,
            title="t",
            statement="s",
            kind="rule",
            explicit=True,
            status="ready_for_delivery",
        )
        s.add(req)
        s.flush()
        iid = uuid.uuid4()
        default_payload = {
            "issue_type_id": "10004",
            "summary": "Reset password by email",
            "description_adf": adf.doc(adf.paragraph("desc")),
            "evidence_comment_adf": adf.doc(adf.paragraph(f"{marker_for(iid)}:evidence quote")),
        }
        s.add(
            ActionIntent(
                id=iid,
                client_id=client_id,
                requirement_id=req.id,
                kind=kind,
                status=status,
                project_key=project_key,
                marker=marker_for(iid),
                payload=payload or default_payload,
            )
        )
        s.commit()
        return iid


def load(factory, iid):
    s = factory()
    return s, s.get(ActionIntent, iid)


async def test_create_success_maps_and_comments(factory, client_id):
    fake = FakeJira()
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded" and intent.jira_key.startswith("SCRUM-")
    issue = fake.issues[intent.jira_key]
    assert marker_for(iid) in issue["fields"]["labels"]
    assert len(fake.comments[intent.jira_key]) == 1  # evidence comment
    m = s.scalars(select(JiraIssueMapping).where(JiraIssueMapping.client_id == client_id)).one()
    assert m.jira_key == intent.jira_key and m.relation == "primary"


async def test_timeout_after_successful_create_does_not_duplicate(factory, client_id):
    fake = FakeJira()
    fake.create_faults = ["timeout_after_apply"]
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded"
    assert len(fake.issues) == 1  # reconciled by marker, never re-created
    assert len([c for c in fake.calls if c == ("POST", "/rest/api/3/issue")]) == 1


async def test_search_lag_is_waited_out_before_deciding(factory, client_id):
    fake = FakeJira()
    fake.create_faults = ["timeout_after_apply"]
    fake.search_empty_polls = 2  # index lags for two polls
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded" and len(fake.issues) == 1


async def test_timeout_before_apply_retries_only_after_confirmed_absent(factory, client_id):
    fake = FakeJira()
    fake.create_faults = ["timeout_before_apply"]
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    ex = make(fake)
    with pytest.raises(JiraUnknownOutcomeError):
        await ex.execute(s, intent)  # reconcile found nothing -> surfaced for job retry
    assert intent.status == "unknown_outcome" and not fake.issues
    await ex.execute(s, intent)  # next attempt: reconcile first (absent), then create once
    assert intent.status == "succeeded" and len(fake.issues) == 1


async def test_5xx_after_apply_is_unknown_outcome_not_failure(factory, client_id):
    fake = FakeJira()
    fake.create_faults = ["500_after_apply"]
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded" and len(fake.issues) == 1


async def test_crash_between_apply_and_persist_is_recovered_on_restart(factory, client_id):
    fake = FakeJira()
    iid = new_intent(factory, client_id)
    # Simulate: intent committed as 'executing', Jira applied the create, process died.
    s, intent = load(factory, iid)
    intent.status, intent.attempts = "executing", 1
    s.commit()
    fake._new_issue({"labels": [intent.marker], "summary": "x"})
    s2, again = load(factory, iid)
    await make(fake).execute(s2, again)
    assert again.status == "succeeded" and len(fake.issues) == 1
    assert [c for c in fake.calls if c == ("POST", "/rest/api/3/issue")] == []


async def test_validation_error_fails_definitively_without_retry(factory, client_id):
    fake = FakeJira()
    fake.create_faults = ["400"]
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "failed" and "JiraValidationError" in intent.last_error
    assert not fake.issues


async def test_non_allowlisted_project_never_touches_jira(factory, client_id):
    fake = FakeJira()
    iid = new_intent(factory, client_id, project_key="OTHER")
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "failed" and fake.calls == []


async def test_kill_switch_holds_without_any_call(factory, client_id):
    fake = FakeJira()
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent, writes_allowed=False)
    assert intent.status == "held" and fake.calls == []


async def test_dry_run_and_terminal_intents_make_no_calls(factory, client_id):
    fake = FakeJira()
    for st in ("dry_run", "succeeded", "rejected"):
        iid = new_intent(factory, client_id, status=st)
        s, intent = load(factory, iid)
        await make(fake).execute(s, intent)
    assert fake.calls == []


async def test_rate_limit_leaves_intent_retryable_and_not_applied(factory, client_id):
    fake = FakeJira()
    fake.rate_limit_next = 1
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    ex = make(fake)
    with pytest.raises(JiraRateLimitedError):
        await ex.execute(s, intent)
    assert intent.status == "approved" and not fake.issues
    await ex.execute(s, intent)
    assert intent.status == "succeeded" and len(fake.issues) == 1


async def test_comment_failure_does_not_fail_or_duplicate_the_intent(factory, client_id):
    fake = FakeJira()
    fake.fail_comments = True
    iid = new_intent(factory, client_id)
    s, intent = load(factory, iid)
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded" and len(fake.issues) == 1


async def test_update_keeps_prior_version_and_is_idempotent(factory, client_id):
    fake = FakeJira()
    existing = fake._new_issue(
        {"summary": "Old", "description": adf.doc(adf.paragraph("old text")), "labels": []}
    )
    payload = {
        "jira_key": existing["key"],
        "summary": "New",
        "description_adf": adf.doc(adf.paragraph("new text")),
        "evidence_comment_adf": adf.doc(adf.paragraph("evidence")),
    }
    iid = new_intent(factory, client_id, kind="update_issue", payload=payload)
    s, intent = load(factory, iid)
    fake.update_faults = ["timeout_after_apply"]
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded"
    assert intent.prior_snapshot["summary"] == "Old"
    assert "old text" in intent.prior_snapshot["description_text"]
    assert fake.issues[existing["key"]]["fields"]["summary"] == "New"
    assert len([c for c in fake.calls if c[0] == "PUT"]) == 1  # not re-applied after reconcile


async def test_add_reference_comment_is_idempotent(factory, client_id):
    fake = FakeJira()
    existing = fake._new_issue({"summary": "Existing", "labels": []})
    iid = new_intent(
        factory,
        client_id,
        kind="add_reference",
        payload={"jira_key": existing["key"], "comment_adf": adf.doc(adf.paragraph("placeholder"))},
    )
    s, intent = load(factory, iid)
    intent.payload = {
        "jira_key": existing["key"],
        "comment_adf": adf.doc(adf.paragraph(f"{intent.marker} meeting reference")),
    }
    s.commit()
    await make(fake).execute(s, intent)
    assert intent.status == "succeeded" and len(fake.comments[existing["key"]]) == 1
    # re-running a finished intent changes nothing
    await make(fake).execute(s, intent)
    assert len(fake.comments[existing["key"]]) == 1


def test_client_has_no_delete_or_sprint_path():
    import inspect

    from app.integrations.jira.client import JiraClient

    names = {n.lower() for n, _ in inspect.getmembers(JiraClient, inspect.isfunction)}
    for n in names:
        for forbidden in ("delete", "sprint", "version", "release", "permission_scheme", "role"):
            assert forbidden not in n, n
    src = inspect.getsource(JiraClient)
    assert '"DELETE"' not in src and "'DELETE'" not in src
