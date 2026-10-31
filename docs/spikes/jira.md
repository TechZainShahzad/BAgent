# Spike B — Jira Cloud

Status: PASSED (basic), 2026-10-18. Site `<your-site>.atlassian.net (sandbox site)`, project `SCRUM` ("QA Testing", team-managed/next-gen, software). Token name "BA" (account-owned; replace with a service account for production).

## Verified
- Auth: HTTP Basic (email + API token) -> `GET /rest/api/3/myself` 200. (First token returned 401; replaced.)
- Issue types: Epic (10001, hierarchy level 1), Task (10003) and Story (10004) at level 0, Subtask (10002, level -1). Hierarchy: Epic > Story/Task > Subtask.
- Workflow statuses (all types): To Do, In Progress, In Review, Done.
- Story create required fields: project, issuetype, summary, reporter (auto-filled). Optional: labels, parent, description (ADF).
- Permissions for the token user on SCRUM: CREATE, EDIT, TRANSITION, ADD_COMMENTS, and DELETE_ISSUES (note: DELETE is granted by Jira, but the platform has no delete code path by design).
- Idempotency marker: created SCRUM-5 with label `ba-intent-<hex>`; `POST /rest/api/3/search/jql` with `labels = "<marker>"` returned it on the first attempt, 2.4 s after the create call began (single sample, so not proof of no lag). Design stays: on unknown outcome, search by marker with a bounded wait/poll before any retry; never blind-retry.
- Search endpoint is the new `/rest/api/3/search/jql` (POST, token-paginated).

## Not yet verified
- Issue entity properties as an alternative marker; rate-limit headers behavior; multiple-sample search lag under load; comments API in ADF; transition IDs.

## Artifacts
`docs/spikes/fixtures/` holds project, createmeta and story-field payloads (no secrets). `spike_issue.txt` lists the spike issue SCRUM-5 (safe to delete manually).

## Rotate
Both tokens were pasted in chat; revoke the old one and treat the current one as exposed once the spike phase ends.
