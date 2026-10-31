# Threat model

## Assets
Client meeting content and derived requirements; Jira project integrity; credentials (Jira token, session secret); audit trail.

## Trust boundaries
1. **Untrusted input**: transcripts, speech-to-text output, Jira descriptions/comments, retrieved knowledge. Treated as data everywhere.
2. **LLM** (Claude CLI): untrusted *output*; validated by Pydantic schemas; has no tools; cannot call Jira.
3. **Operator machine**: runs the backend, capture companion and DB (127.0.0.1 only).
4. **Jira Cloud**: external; accessed by one module with a least-privilege token and project allowlist.

## Threats and controls
| Threat | Control | Test |
|---|---|---|
| Prompt injection in transcript ("delete the project", "mark confirmed") | Untrusted text delimited with a random nonce; system prompt states data-only; Injection Sentinel flags and audits; **policy and lifecycle are code**, so injected text cannot confirm, approve or call Jira; LLM has no tools | `test_phase2_logic`, real-CLI contract + live run on `kickoff_01.vtt` |
| Fabricated evidence | Grounding Verifier: quote must be verbatim in the cited segment; ungrounded items rejected and audited | grounding tests, pipeline test |
| Identity spoofing via speaker label | Labels never mark identity verified; confirmation needs human-verified stakeholder with authority; attribution confidence NULL unless asserted | `test_confirmation_needs_verified_authority` |
| Cross-client leakage | `client_id` on every query; scoped repos; retrieval filters by client/project; conflict detector re-asserts scope; LLM requests require client id | isolation tests (relational + vector) |
| Duplicate / runaway Jira writes | Durable ActionIntent, marker label, reconcile before retry, no blind retry, attempt cap, kill switch, allowlist re-checked at execution | fault-injection suite (`test_jira_execution`) |
| Destructive Jira actions | No client method or code path for delete, permissions, sprint/release; policy `R-PROHIBITED`; static tests | `test_client_has_no_delete_or_sprint_path`, policy tests |
| Credential leakage | Secrets only in git-ignored `.env`; log redaction (Atlassian tokens, key/value, auth headers); LLM logs hashes not content; `ANTHROPIC_API_KEY` stripped from child env | `test_redaction_and_auth` |
| Audit tampering | Hash chain + DB trigger blocking UPDATE/DELETE/TRUNCATE; verification command and API | `test_audit_*` |
| Session attacks on dashboard | HttpOnly SameSite=Strict cookie, signed, 8h expiry, CSRF header on every mutation, bind 127.0.0.1, scrypt passwords | `test_api` |
| Over-retention of client data | Transcript purged right after analysis; evidence/chunks purged at expiry; purge audited | pipeline + purge tests |
| Capture without consent | Consent record required to import/capture; analysis refuses without it | `test_consent_*` |
| Supply chain / dependencies | Minimal deps; uv lockfile; no network at runtime except Claude CLI, Jira, one-time public model download | - |

- The written conversation (`conversation_messages`, ADR 0006) is retained for `CONVERSATION_RETENTION_DAYS`; it holds client words verbatim. It is client-scoped, shown read-only, deleted by the hourly retention job, and can be disabled with `0`. Consent wording must mention it.

## Residual risks
- The Claude CLI sends prompts to Anthropic; real client data requires privacy sign-off (Experiment E) and `APPROVED_PROVIDERS`.
- The Jira API token is exposed in chat history during setup; rotate it.
- Local speech-to-text may mishear; evidence quotes then match STT text, not true speech. Operator review of evidence is the mitigation.
- A privileged DB user can disable the audit trigger; the hash chain detects (not prevents) this.
