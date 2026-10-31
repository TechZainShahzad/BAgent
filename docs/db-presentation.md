# Showing the database in a presentation

## Option 1: Adminer (web UI, nothing to install)
```powershell
cd D:\BaAi
.\scripts\db-viewer.ps1                                   # creates the READ-ONLY login (safe on screen)
docker compose --profile tools up -d adminer              # starts the web UI
```
Open **http://127.0.0.1:8080** and sign in:

| Field | Value |
|---|---|
| System | PostgreSQL |
| Server | `db` |
| Username | `ba_viewer` |
| Password | `viewer_demo` |
| Database | `ba` |

`ba_viewer` can read every table **except `users`** (password hashes) and cannot change anything (every transaction is read-only and it has no write rights). Stop the UI afterwards: `docker compose stop adminer`. Re-run `db-viewer.ps1` after new migrations so new tables are visible.

## Option 2: a desktop client (DBeaver / pgAdmin / VS Code "PostgreSQL" extension)
Host `127.0.0.1`, port `5433`, database `ba`, user `ba_viewer` / `viewer_demo` (read-only), or `ba` / `ba_dev_password` (full access, do not show on screen).

## Option 3: terminal
```powershell
docker exec -it baai-db-1 psql -U ba_viewer -d ba      # password: viewer_demo
```

## A 5-minute story to tell (copy these queries)
```sql
-- 1. Tenants and what was said (the readable record behind the dashboard chat)
select c.name as client, m.title, m.mode, count(*) as messages,
       count(*) filter (where cm.role = 'agent') as from_ba
from conversation_messages cm join meetings m on m.id = cm.meeting_id join clients c on c.id = cm.client_id
group by 1,2,3 order by 1;

select idx, role, speaker, left(text, 90) as text, said_at
from conversation_messages where meeting_id = (select id from meetings order by created_at desc limit 1) order by idx;

-- 2. Every requirement has verbatim evidence (nothing is invented)
select r.title, r.status, e.quote, e.speaker_label, e.attribution_source, e.confirmation_quote
from requirements r join evidence e on e.requirement_id = r.id order by r.created_at limit 10;

-- 3. Why the system did or did not act (deterministic policy, with rule ids)
select outcome, rule_ids, reason, inputs->>'mode' as mode, created_at from policy_decisions order by created_at desc limit 10;

-- 4. What was created in Jira, and the idempotency marker that prevents duplicates
select kind, status, jira_key, marker, attempts from action_intents order by created_at desc limit 10;

-- 5. Every LLM call is accounted for (hashes, never prompts)
select agent, model, duration_ms, cost_usd, exit_class from llm_call_logs order by created_at desc limit 10;

-- 6. Tamper-evident history (each row hashes the previous one)
select seq, event_type, left(prev_hash, 8) as prev, left(hash, 8) as hash from audit_events order by id desc limit 10;

-- 7. Durable jobs (everything survives a restart)
select kind, status, attempts, last_error from jobs order by created_at desc limit 10;

-- 8. Privacy by design: the working transcript is gone after analysis
select m.title, m.transcript_purged_at is not null as transcript_purged,
       (select count(*) from transcript_segments t where t.meeting_id = m.id) as working_segments_left
from meetings m order by m.created_at desc limit 5;
```
Tables worth naming out loud: `conversation_messages`, `requirements` + `requirement_versions` + `evidence`, `action_intents` + `jira_issue_mappings`, `policy_decisions`, `audit_events`, `knowledge_chunks` (pgvector embeddings).

## Safety note
Do not present the `ba` superuser login or `.env`. The viewer role exists so the database can be shown without risking edits or secrets.
