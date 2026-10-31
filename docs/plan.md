# Execution Plan — Agentic BA SDLC Platform

Status: v1 draft, 2026-10-18. Companion to `CLAUDE.md` (which wins on conflict) and `docs/adr/0001-plan-v1-amendments.md`.
Facts about Teams, Graph, Jira and the Claude CLI below are **hypotheses until the Phase 0 spike for that item records evidence** in `docs/spikes/`. Nothing here should be built on an unverified hypothesis.

---

## 1. Goal and success measures

An AI Business Analyst that listens to (or reads) client meetings, builds persistent per-client product knowledge, finds gaps and contradictions, asks focused questions, and creates/updates Jira issues safely, with evidence, authority checks, audit and crash recovery.

| Measure | Target | Kind |
|---|---|---|
| Material requirements/Jira mutations with a linked, verified evidence record | 100% | Hard invariant (test-enforced) |
| Evidence quotes that are verbatim in the source transcript segment | 100% | Hard invariant |
| Duplicate Jira issues after forced timeout/restart | 0 | Hard invariant |
| Cross-client data in any retrieval/prompt | 0 | Hard invariant |
| Silent failures (provider/CLI/Jira/media) | 0 | Hard invariant |
| Extraction precision/recall, question usefulness, duplicate-question rate | Threshold from measured baseline on labeled set | Soft; set in Phase 2, never invented |
| Human edit rate on drafted stories (propose mode) | Tracked, trend down | Soft |

---

## 2. Transparency contract ("full transparency", made testable)

Transparency is a feature set with acceptance tests, not a slogan.

| # | Commitment | Mechanism |
|---|---|---|
| T1 | Participants know an AI is present and what it does | Disclosed named participant/banner; consent record per meeting; capture refused without it |
| T2 | Every claim traces to a source | `Evidence` rows with timestamp, speaker, attribution confidence; **Grounding Verifier** rejects non-verbatim quotes |
| T3 | Every decision is explainable | Each policy decision stores inputs, rule IDs fired, outcome and reason (`PolicyDecision`); dashboard "Why?" panel |
| T4 | Every external action is recorded before and after | `ActionIntent` + append-only `AuditEvent`; hash-chained for tamper evidence |
| T5 | Every LLM call is accountable | `LLMCallLog`: agent, prompt template version, schema version, model, duration, exit class, input/output hashes (content only if `LOG_LLM_CONTENT=true`) |
| T6 | Uncertainty is shown, not hidden | Explicit vs inferred flag, attribution confidence, "unverified speaker" badge, degraded-meeting banner |
| T7 | Attendees can correct the record | Post-meeting minutes (decisions, open questions, actions) shareable with attendees; corrections create new evidence/versions, not edits |
| T8 | Retention is visible and enforced | Per-evidence expiry shown; purge job emits audit events; purge report in dashboard |
| T9 | Humans can see and override autonomy | `AUTONOMY_MODE` + per-project policy displayed; kill switch pauses all external writes |
| T10 | Jira shows its provenance | Every created/updated issue carries label `ba-intent-<id>`, a meeting-evidence comment, and a link back to the dashboard record |

---

## 3. Meeting capture strategy

Principle: **the pipeline is source-agnostic.** Everything downstream consumes `MeetingSource` events (`segment`, `speaker_change`, `pause`, `meeting_started/ended`, `stream_degraded`). Capture routes are interchangeable adapters, ordered by risk.

| Route | What it is | Pros | Cons / gates | Verdict |
|---|---|---|---|---|
| **R0 `transcript_file`** | Import .vtt/.docx/.txt; replay simulator with timing | Zero external risk; drives all dev/test | Not live | **Build first** (Phase 2) |
| **R1 `teams_post_meeting`** | Graph: meeting + transcript after the call | Official, low-risk, speaker names from Teams | Tenant admin consent + app access policy; delay after meeting; Teams only | **Phase 4a** if S3 passes |
| **R2 `local_capture` (own tool)** | Windows companion captures mic + system loopback on the BA's machine, local STT, streams segments to the local backend | Works with **any** meeting tool (Teams, Zoom, Meet); no tenant bot approval; audio never leaves the machine; true live | Diarization quality; **consent/disclosure is on the human operator**; speaker identity weaker | **Phase 4b, recommended live route** |
| **R3 `teams_live` bot** | Real-time media bot joins as participant | Native participant; can post chat | Heaviest: Windows hosting for media SDK, public endpoint/tunnel, Azure bot registration, tenant approval, STT cost | Only if S4 passes and R2 proves insufficient |
| **R4 `own_room`** | Self-hosted WebRTC room (e.g. LiveKit) clients join via link | Full control: per-speaker tracks (perfect diarization), chat, disclosure UX | Client must adopt our link; infra to run | Option for clients who accept it; later |
| R5 Third-party meeting-bot API | Hosted bot joins any platform | Fast | Client audio to another processor; must be in `APPROVED_PROVIDERS`; cost | Not planned; revisit only with privacy sign-off |

### Interaction modes for live meetings (safer default first)

| Mode | Behavior | Default |
|---|---|---|
| **Observer** | Transcribe and analyze; no questions | Fallback when degraded or consent partial |
| **Copilot** | Elicitation questions appear **privately to the human BA** (dashboard / side panel). The human asks aloud or types in chat | **Default for live** |
| **Participant** | System posts questions to meeting chat itself, gated by policy, pause detection, consent, per-meeting enablement | Opt-in per project; needs ADR before enabling; TTS speech needs separate ADR |

Why Copilot first: it gives most of the value (gaps found live, ranked, deduplicated against knowledge) with the human as the speaker of record, which removes the identity/consent risk of an AI talking in a client meeting and still works if live transcription is imperfect.

**Decision (2026-10-18, ADR 0002):** the real-meeting path is **R0 + R2**. No Microsoft 365 tenant is available and R2 is free and tool-agnostic, so R1 (Teams Graph), R3 and R4 are deferred until a tenant exists. Speech-to-text is local and free (`faster-whisper` behind `SpeechProvider`). Diarization is heuristic and always marked low-confidence: the mic channel is the operator, the loopback channel is "remote participants", finer splitting only if a free local diarizer proves adequate in spike D1 (Authority Resolver never confirms on low-confidence speakers).

---

## 4. Architecture (amended)

Unchanged: Python modular monolith, PostgreSQL+pgvector source of truth, FastAPI, React, replaceable adapters, no Kafka/Redis/Celery.
Added: a **Local Capture Companion** (small Windows process, R2) and an **event bus implemented as the Postgres job/event tables** (no new infra).

```
 R0 file ─┐
 R1 Graph ┼─► MeetingSource port ─► Ingestion ─► Consent&Privacy Guard ─► Injection Sentinel
 R2 local ┤                         (events, dedupe)                              │
 R3/R4 ───┘                                                                       ▼
                                   Meeting Intelligence (segments, speakers, pauses)
                                                      │
                  ┌───────────────────────────────────┼───────────────────────────┐
                  ▼                                   ▼                           ▼
        Requirements Analyst                Client Knowledge            Stakeholder Authority
                  └──────────► Grounding Verifier ◄───┘                           │
                                      │                                           │
                                      ▼                                           │
          Elicitation ◄── Conflict & Gap Detector ──► Decision Log ◄──────────────┘
              │ (Copilot panel / chat)                       │
              ▼                                              ▼
                                  Story Architect ─► Testability Reviewer
                                                      │
                                                      ▼
                                 Policy Engine (deterministic) ─► ActionIntent
                                                      │
                                                      ▼
                                   Jira Execution ─► Reconciler ─► Jira Cloud
                                                      │
                           Audit chain · LLMCallLog · Explainer · Dashboard (same service layer as CLI/API)
```

### 4.1 Agent roster

`LLM` = uses `LLMProvider`. `Det` = deterministic code. LLM output is always schema-validated; an LLM may advise but never decides policy.

| # | Agent / module | Type | Responsibility | New? |
|---|---|---|---|---|
| 1 | Meeting Intelligence | Det | Segment, speaker map, timestamps, pause detection, question-slot scheduling | existing |
| 2 | **Consent & Privacy Guard** | Det | Blocks processing without recorded consent; redacts configured PII patterns before LLM; enforces `APPROVED_PROVIDERS` and retention classes | new |
| 3 | **Injection Sentinel** | Det + optional LLM classifier | Flags instruction-like content in untrusted text; wraps/quarantines; never lets flagged text alter policy; findings are surfaced | new |
| 4 | Requirements Analyst | LLM | Extract goals, actors, rules, constraints, NFRs; explicit vs inferred; candidate evidence | existing |
| 5 | **Grounding Verifier** | Det | Each evidence quote must be a whitespace-normalized verbatim substring of the cited segment; timestamps in range; failures reject the item and are logged | new |
| 6 | **Stakeholder Authority Resolver** | Det | Maps speaker → Stakeholder, role, decision authority, verification status; gates `confirmed` | new |
| 7 | Client Knowledge | LLM + Det | Scoped retrieval (client/project), current vs superseded, duplicates | existing |
| 8 | **Conflict & Gap Detector** | Det first, LLM assist | Contradictions vs approved rules, missing actors/NFR/edge cases, ranked by impact; creates conflict records, never overwrites | new (split from Knowledge/Elicitation) |
| 9 | Elicitation | LLM | One question at a time; priority; never repeat answered; targets Copilot/Participant output | existing |
| 10 | **Decision Log Keeper** | LLM + Det | Confirmed vs tentative decisions, supersession chain, minutes draft | new |
| 11 | Story Architect | LLM | Epics/stories/tasks fitted to the real discovered hierarchy; dependencies | existing |
| 12 | **Testability Reviewer** | LLM + Det | Acceptance criteria measurable? Ambiguous words flagged ("fast", "easy"); gates `ready_for_delivery` | new |
| 13 | Quality & Policy Engine | Det | `execute` / `clarify` / `escalate` / `reject` with stored reasons | existing |
| 14 | Jira Execution | Det | Search, build change, intent, execute, map, audit | existing |
| 15 | **Reconciler** | Det | Resolves unknown outcomes by marker search; detects Jira drift (issue edited/deleted/moved) and flags | new |
| 16 | **Explainer** | Det (renders stored data) | Builds the "Why?" trace from `PolicyDecision`, evidence, `LLMCallLog` — no LLM, so it cannot hallucinate reasons | new |
| 17 | **Eval & Replay Harness** | Det | Replays labeled transcripts through fake/real provider, scores metrics, guards regressions | new |

MVP merge allowed: Requirements Analyst + Elicitation + Decision Log in one LLM call with three separate schemas (per `CLAUDE.md` §6).

### 4.2 Key invariants in code (not prompts)

1. `Evidence` cannot be inserted unless the Grounding Verifier passed (DB check via service layer + test).
2. `Requirement.status = confirmed` requires an authority record from the Authority Resolver.
3. Only `integrations/jira` imports the Jira HTTP client (import-linter rule in CI).
4. Every repository method takes `client_id`; a test enumerates repositories and fails on any method lacking it.
5. Audit events are append-only (DB trigger blocks UPDATE/DELETE) and hash-chained.

---

## 5. Data model additions (on top of `CLAUDE.md` §5)

`ConsentRecord`, `MeetingEvent` (stable ID, unique), `TranscriptSegment` (short-lived, retention-bound), `PolicyDecision`, `LLMCallLog`, `ConflictRecord`, `Minutes` (draft/sent/corrected), `PromptTemplateVersion`, `JiraProjectSchemaSnapshot`, `AutonomyOverride`, `PurgeRun`. Build each only with the workflow that uses it; log in `docs/data-model.md`.

Retention (decided 2026-10-18): **full transcript segments are purged immediately after the meeting's analysis completes** (grace period default 0, configurable per client via `TRANSCRIPT_GRACE_HOURS`). Grounding verification therefore runs *during* processing, never later. Approved evidence excerpts + timestamps persist per client retention policy. Live mode keeps only a rolling in-memory window plus the durable segments needed for the active meeting; on crash they are purged on restart recovery after the workflow finishes or fails.

---

## 6. Phased execution plan

Sizing is relative (S ≤ 1 day, M ≈ 2–3 days, L ≈ 1 week, per focused engineer). Each work package (WP) is independently reviewable and leaves the system runnable (principle 9).

### Phase 0 — Feasibility (timebox 2 weeks; spikes run in parallel)

Exit: each spike has `docs/spikes/<name>.md` with commands run, evidence, blockers, fallback. Gate decisions recorded as ADRs.

| ID | Spike | Questions to answer | Output |
|---|---|---|---|
| A | Claude CLI (`claude-cli.md`) | Verified headless/JSON/stdin/model/tool-disable flags from `claude --help`; env var stripping works; exit codes & messages for not-logged-in, usage limit, bad model; latency & reliability extracting a 30-min transcript to a schema; concurrency behavior | Verified command line + error taxonomy |
| B | Jira Cloud (`jira.md`) | Sandbox project; discover issue types/hierarchy/required fields/transitions; token permissions; is `labels` searchable immediately after create (JQL lag)? entity properties searchable?; rate limits; ADF description format | Idempotency design confirmed, discovery payloads saved as fixtures |
| C | Teams post-meeting (`teams.md`) — **deferred, no M365 tenant** | App registration, minimal Graph permissions, tenant admin consent/app access policy needed, transcript availability delay, speaker attribution fields, VTT shape | Go/no-go for R1 + recorded fixtures |
| D | Live capture (`bridge.md`) | (D1) R2: WASAPI loopback + mic capture on this machine, local STT latency/word-error on meeting audio, diarization options, with Teams/Zoom/Meet desktop. (D2) R3: media-bot feasibility, hosting and approval cost, only paper study unless D1 fails | Go/no-go R2; decision on R3/R4 |
| E | Privacy & approval (`privacy.md`) | Approved providers list; consent wording; retention classes; who may supply real client data; what leaves the machine (only Claude CLI prompts, with redaction) | Signed-off policy; gates real data |
| F | Local embeddings | Model choice, Windows install, speed, dimension; pgvector index choice | Chosen `EmbeddingProvider` |

Parallel with spikes (allowed because they are reversible and are Phase 1 content): repo bootstrap WP1.1.

### Phase 1 — Foundation (Exit: clean clone → green tests on Windows)

| WP | Deliverable | Acceptance |
|---|---|---|
| 1.1 | Repo scaffold per `CLAUDE.md` §4, `uv`/pyproject, ruff, mypy strict (domain, orchestration), pre-commit, `scripts/dev.ps1`, `scripts/test.ps1`, import-linter contracts (dependency direction; Jira import restriction) | `scripts/test.ps1` green on fresh clone |
| 1.2 | Docker Compose Postgres+pgvector, Alembic baseline, Testcontainers/compose test fixture | Integration test creates and migrates DB |
| 1.3 | Config (`pydantic-settings`), `.env.example`, secret redaction in logs, structured JSON logging with correlation IDs | Test proves secrets never logged |
| 1.4 | Core persistence: Client, Project, user/auth, unit of work, repository base requiring `client_id` | Isolation meta-test enumerates repos |
| 1.5 | Job runner: jobs table, `FOR UPDATE SKIP LOCKED`, retries/backoff, poison → `failed`, startup resume, unique event IDs | Recovery tests: kill mid-job, duplicate event |
| 1.6 | Audit chain + `PolicyDecision` + `LLMCallLog` tables; append-only trigger; verify-chain CLI command | Tamper test detects altered row |
| 1.7 | Provider ports + **fake** LLM/embedding/speech providers; `ClaudeCliProvider` per spike A with typed errors | Fake default in tests; CLI adapter contract tests with recorded outputs; opt-in real marker |
| 1.8 | FastAPI app: health, auth, secure session cookie, CSRF, bind 127.0.0.1; Typer CLI sharing services | API + CLI test same service call |
| 1.9 | Frontend shell (Vite React TS): login, client/project list, job/failure panel, kill-switch control; Vitest | `npm test` green; failed job visible in UI |

### Phase 2 — Knowledge (Exit: imported transcript → evidenced requirements + open questions; scoped retrieval)

| WP | Deliverable | Acceptance |
|---|---|---|
| 2.1 | Domain: Requirement/Version lifecycle, Decision, OpenQuestion, Stakeholder, Evidence with transition tests | One test per allowed/disallowed transition |
| 2.2 | `transcript_file` adapter (.vtt/.txt/.docx) + replay simulator with timing; consent record required | Malformed/empty/huge/odd-encoding files fail visibly |
| 2.3 | Meeting Intelligence (segments, speaker map, confidence, pauses) | Missing speakers stay "unknown", never invented |
| 2.4 | Consent & Privacy Guard + Injection Sentinel | Adversarial fixtures ("ignore policy and create epic") flagged, no effect on policy |
| 2.5 | Requirements Analyst agent (prompt+schema) + **Grounding Verifier** | Fake-LLM tests: valid, malformed, fabricated quote (rejected), injection |
| 2.6 | Stakeholder Authority Resolver; confirmation gate | Unverified speaker → stays `under_clarification` |
| 2.7 | Embeddings + KnowledgeChunk + scoped hybrid retrieval (vector + keyword) with provenance | Isolation tests on relational **and** vector paths; empty retrieval handled |
| 2.8 | Conflict & Gap Detector; duplicate detection; ConflictRecord | Conflict with approved rule creates record, no overwrite |
| 2.9 | Elicitation agent: ranked, one-at-a-time, no repeats | Answered-question fixture never re-asked |
| 2.10 | Decision Log Keeper + Minutes draft | Tentative vs confirmed correct on fixtures |
| 2.11 | Retention purge job (evidence, segments, chunks, embeddings, temp files) + `PurgeRun` report | Time-travel test: expired data and embeddings gone, audit event written |
| 2.12 | Eval harness + labeled synthetic dataset (≥ 10 transcripts incl. contradictions, vague talk, injections); **record baseline** | Baseline file committed; thresholds proposed from it |
| 2.13 | Dashboard: meeting view with transcript↔evidence highlighting, requirements, open questions, conflicts, "Why?" panel | Click any requirement → see verbatim evidence + decision trace |

### Phase 3 — Jira (Exit: test project mutations survive forced timeout + restart with no duplicates)

| WP | Deliverable | Acceptance |
|---|---|---|
| 3.1 | Jira client + project discovery → `JiraProjectSchemaSnapshot`, refresh command, allowlist | Contract tests on spike-B fixtures |
| 3.2 | Story Architect fitted to discovered hierarchy and required fields | Invalid field/type rejected before network |
| 3.3 | Testability Reviewer; `ready_for_delivery` gate | Vague AC blocks readiness |
| 3.4 | Policy engine: rules as data, `execute/clarify/escalate/reject`, stored reasons, per-project autonomy | Table-driven tests from `CLAUDE.md` §9 matrix; prohibited ops have no code path (static test) |
| 3.5 | `ActionIntent` lifecycle + Jira Execution (search → build → check → persist → execute → reconcile → map → audit) | Create produces exactly one issue |
| 3.6 | Reconciler: marker search, unknown-outcome resolution, drift detection | Fault injection: timeout after successful create, crash between execute and persist, duplicate event |
| 3.7 | Modes `dry_run` → `propose` (dashboard approve/reject/edit) → `autonomous`; kill switch | Mode switch audited; dry-run makes zero HTTP writes (test asserts) |
| 3.8 | Update flow with prior-version record + evidence comment; link-back to dashboard | Version history visible; Jira shows provenance |
| 3.9 | Dashboard: proposals queue, intent states, audit viewer, chain verification | Approving a proposal executes exactly once |

### Phase 4 — Meetings (Exit: real authorized test meeting runs the workflow; simulation alone does not count)

| WP | Deliverable | Acceptance |
|---|---|---|
| 4.1 | R1 `teams_post_meeting` (Graph auth, fetch, map to `MeetingSource`) | Real authorized meeting ingests end to end |
| 4.2 | R2 Local Capture Companion: capture, local STT behind `SpeechProvider`, authenticated TLS/loopback stream to backend, visible "recording" indicator + consent gate | Raw audio never written to disk (test inspects temp dirs) |
| 4.3 | Live pipeline: streaming segmentation, incremental extraction, question scheduling with pause detection | Latency budget recorded; stream drop → meeting marked degraded, questions stop, UI shows it |
| 4.4 | Copilot panel: ranked live suggestions, "asked / skip / answered" feedback feeding back into knowledge | Operator action recorded as evidence of answer |
| 4.5 | Participant mode (chat only) behind ADR + per-project flag | Off by default; only on consent + policy pass |
| 4.6 | Post-meeting minutes to attendees + correction flow | Correction creates new version, old preserved |
| 4.7 | R3/R4 only if ADR 0002 says so | — |

### Phase 5 — Hardening and pilot

Security review and threat-model update, load/soak (long meeting, many clients), backup/restore drill, full recovery matrix from `CLAUDE.md` §11, pilot with one real project in `propose` mode, threshold ratification from eval baseline, runbook, then (and only then) enable `autonomous` for selected low-risk operations.

---

## 7. First sprint (Weeks 1–2)

1. Spikes A and B (unblock everything) — run first.
2. WP1.1, 1.2, 1.3 (scaffold, DB, config/logging).
3. Spike E drafts: consent text and approved-provider list for your sign-off.
4. Spike F; spikes C and D1 start once tenant/test-meeting access is available.
5. Write ADR 0002 (capture route) when C/D1 land.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Claude CLI latency, usage limits or output drift | Spike A; typed errors; bounded concurrency; batch per meeting; API adapter behind same port; schema validation + eval regression |
| Subscription CLI terms/limits for production automation | Verify allowed use; keep API adapter ready (ANTHROPIC_API_KEY, separate class) |
| Teams tenant approval delays | R0 drives all development; R2 avoids tenant dependency |
| Local STT diarization weak | Mark speaker confidence low; Authority Resolver won't confirm; Copilot keeps human in loop; R4 for perfect tracks |
| Recording-consent law varies by jurisdiction | Consent gate is a hard stop; wording signed off in Experiment E; operator-visible indicator |
| Jira search lag defeats idempotency | Spike B; reconcile with bounded wait-and-search before any retry; local intent state is authoritative |
| Prompt injection via transcript or Jira text | Sentinel + delimiting + policy in code + adversarial fixtures + no tools for LLM |
| Scope creep into more agents/infra | New infra only via ADR; each agent needs prompt+schema+tests before merge |

## 9. Decisions log and remaining open items

Resolved 2026-10-18:
1. Live route: own `local_capture` companion (free, tool-agnostic); Teams/M365 routes deferred.
2. Transcript retention: purge right after analysis completes (no grace by default).
3. Jira site: `<your-site>.atlassian.net (sandbox site)`. **Credentials supplied but the API returned HTTP 401 on `/rest/api/3/myself`** — see `docs/spikes/jira.md`. Jira work is blocked on a working token. No M365 tenant.
4. Language: English only (STT model `*.en`, English prompts).
5. Claude subscription use is allowed; prior art exists in `D:gentic_AI` (TestPilot_AI), see section 10.

Still open:
- A working Jira API token (and which project key is the sandbox; confirm test issues may be created there).
- Which meeting tools you actually use day to day (Zoom/Meet/Teams-free/etc.) so spike D1 tests the right one.
- Whether the BA operator machine has a GPU (affects local STT model size and live latency).

---

## 10. Reuse from TestPilot_AI (`D:gentic_AI`)

Proven there and adopted here (re-verified in our own Spike A before relying on them):
- **Claude CLI adapter lessons:** prompt via stdin; `--json-schema` for validated structured output; `--tools ""` to disable built-in tools; `--system-prompt`; `--resume <session>` for carried context (use sparingly: client isolation says one session per client/meeting, never shared across clients).
- **Windows wrinkle:** the global `claude` is a `.cmd` shim; do not use `shell=True`. Resolve the real `claude.exe` the shim wraps and spawn it directly with an argument list (Python: `asyncio.create_subprocess_exec`).
- **Run artifacts:** every stage writes typed JSON artifacts per run plus `run.log` and `llm-calls.jsonl`. We keep that idea but store it in PostgreSQL (`WorkflowRun`, `LLMCallLog`) rather than files, so it is durable, queryable and subject to retention.
- **Guardrails enforced in code, not prompts** (their assertion-weakening guard): same philosophy as our Grounding Verifier and policy engine.
- **Jira ADF to text walker** and Jira Basic auth (email + API token, not Bearer) — reimplement in Python in `integrations/jira/`.
- **Deterministic reporter / bounded loops:** bounded retries and loops are configuration, not constants.
- Their Jira use is read-only; ours writes, so idempotency, reconciliation and policy are new work.

---

## 11. Progress log

**2026-10-18 — Phase 0:** Spike A (Claude CLI) and B (Jira) passed basic checks (`docs/spikes/`). C (Teams) deferred (no tenant). D1 (local capture), E (privacy sign-off), F (embeddings) not started.

**2026-10-18 — Phase 1 complete.** WP1.1–1.9 done and verified with `scripts\test.ps1`: ruff, format, mypy (35+ files), pytest 156 passed (unit, integration on real Postgres, recovery, security, import-linter), vitest 2 passed, frontend build OK. Real Claude CLI contract test (`-m real_llm`) passed. Not yet done from Phase 1: kill-switch persistence/endpoint (setting exists, read-only), pre-commit hooks, git repo.

**2026-10-18 — Phase 2 started (pure logic done, DB wiring pending):** requirement lifecycle with per-transition tests (WP2.1 domain part), `transcript_file` parser for .vtt/.txt/.docx (2.2 parser part), Injection Sentinel (2.4 part), Grounding Verifier + Requirements Analyst with fake-LLM schema/adversarial tests (2.5). Remaining in Phase 2: persistence for meetings/evidence/requirements/questions/stakeholders, consent record, authority resolver, embeddings + scoped retrieval, conflict/gap detector, elicitation, decision log, retention purge job, eval baseline, dashboard meeting view.

**2026-10-18 - Phase 2 wiring:** migration 0002 (stakeholders, meetings, consent, transcript segments, evidence, requirements+versions, open questions, purge runs). Consent-gated import, durable analyze job (consent guard, analyst, grounded persistence, transcript purge right after analysis), authority-gated confirmation, immutable versions, evidence retention purge, per-client runner scoping. 169 backend tests pass. Real run on synthetic transcript tests/eval/fixtures/kickoff_01.vtt with the real Claude CLI: 7 requirements, 4 questions, injection flagged and ignored, transcript purged. Remaining in Phase 2: embeddings + scoped retrieval, conflict/gap detector, elicitation agent, decision log, eval baseline, dashboard meeting view, authority resolver UI/API.

**2026-10-18 - Phase 2 knowledge layer:** migration 0003 (knowledge_chunks with pgvector HNSW, conflict_records). fastembed bge-small-en-v1.5 embeddings (ADR 0003). Scoped retrieval (client/project, current-only, explicit empty result). Conflict & Gap Detector: deterministic candidate selection + LLM relation judge; contradictions with approved requirements block confirmation; nothing auto-resolved. Analysis writes now atomic via savepoint (failure cannot leave duplicates on retry). Elicitation: duplicate/answered question suppression, one-at-a-time ranking. Live run with real Claude + fastembed on two synthetic meetings detected 2 contradictions, 1 duplicate, 2 refinements, 0 false relation for an unrelated requirement. 184 backend tests pass. Remaining Phase 2: eval harness + baseline, decision log, dashboard meeting/evidence/stakeholder views, API endpoints for these services.

**2026-10-18 - Phase 3 complete:** policy engine (exhaustive tests), Jira client/discovery/executor with fault injection (timeout after create, dropped request, 5xx after apply, search lag, 429, crash between apply and persist), Story Architect + Testability Reviewer, ActionIntent lifecycle, dry_run/propose/autonomous, runtime kill switch, LLMCallLog, Explainer, API + dashboard (requirements/why, proposals queue, conflicts, questions, meetings, kill switch). Live run against real SCRUM: dry_run made zero writes; propose -> approve -> worker created SCRUM-6 with marker label, AC, provenance and evidence comment; re-delivery created no duplicate. Real-Jira contract tests (read-only) pass.

**2026-10-18 - Phase 4 (own capture route) complete, with an honest caveat:** live session service (consent gate, heartbeat, degraded mode, pause-controlled one-at-a-time questions, window question mining), local capture companion (WASAPI loopback + mic, VAD, faster-whisper, buffering on API outage, visible device/STT failures), minutes + correction flow. Dress rehearsal through the REAL capture path (TTS voices played on the speakers, `ba capture`, real Whisper, real Claude, real embeddings): 5/5 sentences captured, 4 requirements + 4 questions, speakers left unknown, transcript purged, call logged. This used synthetic voices; a real meeting with human participants has not been run (needs a human operator and participant consent). Teams-native routes deferred (no tenant). Participant (chat) mode not built (ADR 0004).

**2026-10-18 - Phase 5 (hardening) done, pilot not done:**
- Recovery matrix (CLAUDE.md section 11): every case now has a test - restart mid-workflow, provider down, CLI error/invalid output, Jira timeout after successful create, duplicate events, media stream stop (degraded), empty scoped retrieval, requirement superseded/revised mid-workflow (pending action cancelled, never executed stale).
- Soak: 4 competing workers x 150 jobs across 3 clients: every job exactly once, all audit chains valid.
- Backup/restore drill passed (`scripts/backup.ps1`): row counts equal, 4 audit chains re-verified on the restored copy.
- Security review pass: found and removed a truncated live Jira token from a test file; sanitized Jira fixtures; `/health` no longer discloses config; login lockout (5 failures / 5 min per account); security headers; session secret randomized; pre-commit hooks (lint, format, mypy, secret block); git repo initialized (nothing committed).
- Retention: scheduled hourly purge (expired evidence + chunks, orphaned/failed transcript segments; live meetings untouched); `ba purge`.
- Eval baseline measured with real Claude + fastembed on 6 labeled transcripts (`tests/eval/baseline.json`): recall 1.00, precision 0.90, question coverage 1.00, injection leaks 0, ungrounded 0. Caveat: small dataset labeled by the same author; the two precision misses are vague small-talk items ("make the app more modern") that the model extracted as unspecific requirements. Regression guard is opt-in (`-m "real_llm and real_embed"`) with tolerance 0.1.
- NOT done (needs a human / external access): real meeting with human participants and consent; Microsoft Teams routes (no tenant); pilot sign-off and enabling `autonomous` on a real project; speech-to-text WER on real conversations; privacy sign-off (Experiment E) before real client data; credential rotation.

**2026-10-18 - Interview mode + autopilot (ADR 0005):** conversational technical-BA interviewer (13-topic checklist, plain language, options for laymen, confirm-back), confirm-back evidence rule, declared remote speaker, spoken output to a chosen audio device with self-mute and barge-in, Epic-then-linked-Stories autopilot with held-items report, per-requirement blocking questions, technical notes/assumptions section, known-context for drafting. Full rehearsals with a simulated layman (second Claude as a school head teacher, different TTS voice, through real speakers/loopback/Whisper/Claude/Jira): 17 and 22 turn interviews, all 13 topics covered, agent closed and ended by itself; rehearsal 2 created Epic SCRUM-7 with Stories SCRUM-8..14 linked under it, 2 items held with reasons. Bugs found by the rehearsals and fixed: testability heuristic too strict; constraints became stories; meeting-wide blocking questions blocked everything; goal not recognised on autopilot retry; story drafts re-listed answered points as assumptions. Not yet done: spoken output into a real Teams call (needs a virtual audio cable, awaiting owner approval), a human on the other end, barge-in on a real cable, speech accuracy with real voices.

**2026-10-18 - Follow-up calls (persistent product understanding):** found by the user in a real session: the agent said it could not remember earlier calls. Fixed: `services/context.py` builds trusted prior knowledge (earlier calls, requirements + Jira keys, Epic, open questions, covered topics) per client/project, fed to the interviewer; deterministic welcome-back opening; autopilot reuses the existing Epic. 8 new tests (351 total) - they also caught that the history was not reaching the prompt. Verified live: follow-up call on the Teams-test client opened with the history and moved to the open questions. Not yet verified: a real human follow-up call; whether the agent names Jira keys when asked (prompt tightened, untested).
