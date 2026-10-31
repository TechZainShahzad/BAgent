# ADR 0008: The platform may create Jira projects it registers itself

Status: accepted (2026-10-18)

## Context
Each client needs its own Jira project. Creating one by hand and editing `JIRA_PROJECT_ALLOWLIST` for every new client does not scale, and the dashboard (Live capture) is meant to be the single entry point.

## Decision
- `integrations/jira/provision.py` is the only code that creates a Jira project: a team-managed Scrum software project (it has a backlog). It refuses an existing key, checks the token's `ADMINISTER` permission first, honours the kill switch, and there is no delete path.
- A project created this way is stored with `projects.jira_provisioned = true`. `services/jira_projects.effective_allowlist` = `JIRA_PROJECT_ALLOWLIST` union all provisioned keys; policy, executor, discovery and autopilot use it. Projects typed in by hand are NOT auto-allowed.
- The request and the outcome are audited; the request is committed before the Jira call. An unknown outcome (timeout) is reconciled by looking the key up before reporting.

## Consequences
- The API token's user needs Jira administrator rights for this feature (checked via `GET /jira/capabilities`); without it the dashboard offers local registration only.
- Created Jira projects must be deleted by a human in Jira if unwanted.
