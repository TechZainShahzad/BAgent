# Data model and migration log

PostgreSQL 16 + pgvector. All client data tables carry `client_id` (indexed). Repositories and services filter by it; isolation is tested on relational and vector paths.

## Migrations
| Rev | Content |
|---|---|
| 0001 | clients, projects, users, jobs, audit_events (hash-chained; UPDATE/DELETE/TRUNCATE blocked by trigger), policy_decisions, llm_call_logs; `vector` extension |
| 0002 | stakeholders, meetings, consent_records, transcript_segments, requirements, requirement_versions, evidence, open_questions, purge_runs |
| 0003 | knowledge_chunks (`vector(384)` + HNSW cosine index), conflict_records |
| 0004 | system_flags, jira_schema_snapshots, story_drafts, action_intents, jira_issue_mappings |

## Key rules encoded in schema/services
- `jobs`: unique `(client_id, kind, event_id)` makes duplicate events no-ops.
- `audit_events`: unique `(chain_key, seq)`; per-client chain plus a `system` chain; verify with `ba verify-audit`.
- `transcript_segments`: short-lived; deleted right after analysis (`TRANSCRIPT_GRACE_HOURS=0`).
- `evidence`: only created after the Grounding Verifier; `expires_at` drives retention purge; `attribution_confidence` is NULL unless asserted; `stakeholder_id` is set only by a human-verified stakeholder match.
- `requirement_versions`: immutable history; confirmation requires authority + evidence; ready requires a measurable-AC draft for the current version.
- `knowledge_chunks`: derived data; `expires_at` mirrors evidence; deleted by the purge job; `is_current=false` for superseded/rejected/cancelled.
- `action_intents`: unique `marker`; persisted before execution; `prior_snapshot` for updates.
- `conflict_records`: never auto-resolved; contradictions with approved requirements block confirmation.

## Local capture (Phase 4)
Added in 0005 (see below once applied): capture sessions and meeting degradation fields.
