# Jira integration

Verified against the real sandbox project `SCRUM` ("QA Testing", team-managed) on 2026-10-18. Only `app/integrations/jira/` talks to Jira (import-linter contract + static tests).

## Discovery
`ba jira discover <client> <KEY>` -> `discover_project` (allowlist enforced) stores a `JiraSchemaSnapshot`: issue types with ids, `hierarchyLevel`, `subtask`, required fields and field keys (from `createmeta/{KEY}/issuetypes/{id}`), statuses, and the token's permissions. Nothing is assumed: drafts are validated against the snapshot (`domain/jira_schema.py`): unknown type, sub-task, missing required field, unsupported `parent`, unavailable field, summary > 255. Refresh = rerun the command.

## Mutation flow
search existing -> build change -> policy -> schema check -> **persist ActionIntent (committed)** -> execute -> reconcile -> persist mapping -> audit -> evidence comment.

| Step | Implementation |
|---|---|
| Search existing | `search_duplicates`: JQL `summary ~ "<words>"` in the project. One exact normalized match -> `add_reference` intent. Any other match -> `R-DUPLICATE` escalate. Verified against real Jira (`tests/contract/test_jira_real.py`, `-m real_jira`). |
| Policy | `orchestration/policy.py`, pure and exhaustively tested. Stored as `PolicyDecision` with rule IDs, inputs, reason. |
| Modes | `dry_run` (intent stored, zero writes), `propose` (dashboard/CLI approval), `autonomous`. Effective mode = stricter of env, DB override, project. Env is the ceiling. Kill switch (DB flag) holds everything, re-checked at execution time. |
| Intent | `ActionIntent` with unique marker `ba-intent-<24 hex>`. Statuses: dry_run, awaiting_approval, held, approved, executing, unknown_outcome, succeeded, failed, rejected. |
| Execute | `JiraExecutor.execute`. Commits `executing` BEFORE the HTTP write. |
| Reconcile | On timeout / connection drop / 5xx on a write -> `JiraUnknownOutcomeError` -> bounded marker search (JQL `labels = "<marker>"`, backoff) before ANY retry. 429 = not applied, safe retry. 4xx = definitive failure, no retry. Crash between apply and persist: stale `executing` intent is recovered by the job runner and reconciled first. |
| Mapping | `JiraIssueMapping` (primary/reference). A second delivery of a synced requirement never creates another issue (becomes update or `R-UPDATE-EVIDENCE` clarify). |

## Idempotency marker
Label `ba-intent-<id>` on every created or updated issue. Search lag: the sandbox returned a new issue's label on the first poll (single sample, 2026-10-18); the executor still polls with backoff because Jira does not guarantee immediate indexing. Issue entity properties were not needed.

## Updates
`update_issue` fetches the current issue, stores `prior_snapshot` (summary, description text and ADF) on the intent before changing anything, applies fields + marker label in one PUT, then adds an evidence comment. Reconcile checks the marker label.

## Provenance on the issue (T10)
Description ends with a Provenance section (requirement id/version, meeting). A comment tagged `<marker>:evidence` lists each verbatim quote with speaker label, timestamp and whether identity is verified, plus a dashboard link. Comment creation is idempotent and its failure never fails the intent (audited).

## Prohibited
No client method for delete, sprint, release/version or permission changes (static test). `DELETE` is never issued (the Jira double asserts on it). Rate limits: reads retry on 429/5xx (bounded), writes never auto-retry.

## Known limits / not verified
Only Epic/Story/Task creation and parent links via `parent`; custom required fields beyond `summary/issuetype/project/reporter` are detected but not auto-filled (draft is rejected with a clear reason). Epic creation requires a `goal` requirement and hierarchy check. Transitions are not performed. Multi-sample search-lag measurements under load are not done.
