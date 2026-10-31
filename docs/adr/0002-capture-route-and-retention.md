# ADR 0002 — Capture route and transcript retention

Status: Accepted (user decision, 2026-10-18)

## Decisions
1. Real-meeting capture is the own `local_capture` companion (R2), plus `transcript_file` (R0) for dev/test. Chosen because it is free, works with any meeting tool, and no Microsoft 365 tenant is available. `teams_post_meeting`, `teams_live` and `own_room` are deferred until a tenant/need exists; their port and adapter slots stay.
2. Speech-to-text is local and free (`faster-whisper`, English) behind `SpeechProvider`. Speaker identity from loopback capture is low-confidence by construction.
3. Full transcript segments are purged immediately after meeting analysis completes (`TRANSCRIPT_GRACE_HOURS` default 0). Grounding verification happens during processing.

## Consequences
- Operator machine must run the companion; consent/disclosure UX is mandatory and a hard gate.
- Evidence excerpts are the only durable record of what was said, so the Grounding Verifier and excerpt approval are load-bearing.
- Post-purge, re-analysis of a meeting is impossible by design; re-runs need a re-import.
