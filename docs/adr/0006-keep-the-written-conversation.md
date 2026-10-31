# ADR 0006: Keep the written conversation (changes the "transcripts are deleted immediately" rule)

Status: Accepted (user request, 2026-10-18: "store the call conversation in the db and show it in the dashboard, what the client says and what we respond, in a chat view")

## Context
ADR 0002 / CLAUDE.md section 10 froze: raw audio and full transcripts are discarded promptly; only minimal evidence is kept. The working transcript (`transcript_segments`) is still deleted right after analysis. The user now wants the full call visible as a chat for review and presentations.

## Decision
- A **separate table `conversation_messages`** holds the readable record: role (`agent`, `client`, `operator`, `participant`), speaker label, text, channel, offset, timestamp, `expires_at`. The working transcript and its purge are unchanged.
- Written at the same moments as the working transcript (live segments, agent speech, transcript import), and only for meetings with a recorded consent.
- Retention is explicit and configurable: `CONVERSATION_RETENTION_DAYS` (default 365). `0` switches storage off entirely. The hourly retention job deletes expired messages and audits the count.
- **Audio is never stored.** Speaker identity rules are unchanged (declared speakers are shown as declared).
- The agent's spoken disclosure now says it is "keeping a written record of what we discuss on this computer; no audio is saved".
- The dashboard shows it read-only (Conversations screen). Messages are not editable.

## Consequences
- Client words are now retained for up to a year by default. Consent wording must cover this; set the days to match what you promise clients, or `0` to disable.
- Calls recorded before this change have no stored conversation (their transcript was already purged); the UI says so.
- The threat model's data inventory gains one table with the same client scoping and retention rules as evidence.
