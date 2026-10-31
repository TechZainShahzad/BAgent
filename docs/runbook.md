# Runbook

Everything below was exercised on Windows 11 with Docker Desktop (see `scripts\test.ps1` for the automated parts).

## Start / stop
```powershell
.\scripts\dev.ps1 up          # Postgres+pgvector (127.0.0.1:5433)
.\scripts\dev.ps1 migrate     # apply migrations (ba + ba_test)
.\scripts\dev.ps1 api         # API on 127.0.0.1:8000
.\scripts\dev.ps1 worker      # durable job runner (+ stale-stream check, hourly retention purge)
cd frontend; npm run dev      # dashboard on 127.0.0.1:5173 (proxies /api)
```
First-time: `cd backend; python -m uv run ba create-user you@example.com` (password >= 12 chars), `ba create-client "Name"`.
Settings live in `D:\BaAi\.env` (git-ignored). Key ones: `AUTONOMY_MODE` (dry_run|propose|autonomous), `LLM_PROVIDER=claude_cli`, `EMBEDDING_PROVIDER=fastembed`, `JIRA_PROJECT_ALLOWLIST`, `SESSION_SECRET`.

## Daily flows
| Goal | Command |
|---|---|
| Import a transcript | `ba import-transcript <client> file.vtt "Title" --consent-basis "<who attested what>"` then `ba worker --once` |
| Live meeting (Copilot) | API running, then `ba capture <client> "Title" --consent-basis "..." --email you --password ...` (`--list-devices` first; use `--loopback-device` if the meeting app plays to a non-default device). Ctrl-C ends and queues the analysis. |
| Verify a stakeholder | `ba set-stakeholder <client> "Name" --authority --verified --note "how you checked"` (links their evidence) |
| Confirm a requirement | `ba transition <client> <req> confirmed "reason"` (refused unless a verified stakeholder with authority is behind the evidence and no open contradiction with an approved rule) |
| Draft + deliver to Jira | `ba jira discover <client> SCRUM` (once), `ba jira draft ...`, `ba jira deliver ...`, approve in dashboard or `ba jira approve ...`, `ba worker --once` |
| Draft minutes | `ba minutes <client> <meeting> --out minutes.md` |
| Correct a requirement | dashboard / `POST .../requirements/<id>/correct` (new version; approved items return to clarification) |

## Emergency
- **Stop all external writes**: dashboard "Pause all writes" or `ba kill-switch on`. Held intents stay `held`; nothing is lost. Resume with `off`, then approve/execute.
- **Wrong autonomy mode**: env `AUTONOMY_MODE` is the ceiling; lower it and restart. Project mode can only be stricter.
- **Jira rate limiting**: intents stay `approved` and retry with backoff; no action needed.

## Failure handling (all failures are visible)
| Symptom | Where visible | Action |
|---|---|---|
| Analysis failed / provider down | Dashboard Meetings (`failure_reason`) and Jobs (failed); job retries 3x then `failed` | Fix cause (e.g. `claude` login / usage limit), re-import or re-enqueue; transcript is kept for 72h |
| Intent `unknown_outcome` | Proposals panel, `ba jira intents` | Do nothing: the next run reconciles by the `ba-intent-<id>` label before any retry. Never create the issue by hand first. |
| Intent `failed` | Proposals panel with Jira's reason | Usually a draft/schema problem; fix requirement, re-draft, re-propose |
| Meeting `degraded` | Dashboard + `degraded_reason` | Capture stopped or lost; restart `ba capture` (same meeting cannot be resumed from another machine) or end the meeting |
| `API unreachable` in capture | capture console | Transcript is buffered and delivered when the API returns |
| Audit chain `BROKEN` | Dashboard header, `ba verify-audit` | Treat as a security incident: restore from backup and investigate |

## Backup and restore
`.\scripts\backup.ps1 backup` then `.\scripts\backup.ps1 drill` (restores to a scratch DB, compares row counts, re-verifies every audit chain with the app's verifier, drops it). Drill passed on 2026-10-18. Backups contain client data: keep `backups\` out of git (it is ignored) and apply the same retention as the database.

## Retention
Transcript segments are deleted right after analysis (`TRANSCRIPT_GRACE_HOURS`, default 0). The worker purges hourly: expired evidence and derived chunks, orphaned segments (analyzed past grace; failed/degraded past 72h). `ba purge` runs it now. Live meetings are never purged.

## Rotating credentials
Jira: create a new token, update `JIRA_API_TOKEN` in `.env`, revoke the old one. Both tokens used during setup were pasted into a chat and must be revoked. Session: change `SESSION_SECRET` (logs everyone out).

## Quality guard
`python -m uv run pytest -m "real_llm and real_embed" tests/eval` re-runs the real model against the measured baseline (`tests/eval/baseline.json`). Re-measure with `python -m uv run python -m tests.eval.run_baseline` after changing prompts or models.


## Reply speed (live interview)
What the client waits for after they stop talking, and what was done about each part:

| Step | Before | Now |
|---|---|---|
| End of speech detected | 0.7 s | 0.7 s (unchanged) |
| Transcription (`small.en`, CPU) | ~1-1.5 s | unchanged (use `--model base.en` for ~3x faster, a bit less accurate) |
| Send to the server | up to 2 s (batched) | immediate |
| Worker picks the job up | up to 1 s | 0.2 s, in its own lane (never behind an analysis job) |
| Claude call | ~4-5 s (new process every turn) | ~1.3-2 s (warm process per client+meeting, started during the greeting) |
| Pause rule / cooldown | 2.2 s / 1.5 s | 1.3 s / 1.0 s |
| Speech synthesis start | ~0.5 s (new PowerShell) | ~0.1 s (persistent) |

Measure it: `backend\.venv\Scripts\python.exe ..\scripts\measure_reply_latency.py <client_id> <email> <password>` (set `$env:PYTHONPATH="."` first) drives a rehearsal interview through the real API, worker and Claude and prints the wait per turn. During a real call the Live capture screen shows "last reply ready Ns after you stopped".

Warm sessions are per client AND meeting (never shared), recycled every 8 turns and after 2 minutes idle; on any problem the call falls back to a normal one-shot Claude call and a warning is logged.
