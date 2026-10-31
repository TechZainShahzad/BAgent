# ADR 0004 — Live mode is Copilot-only; Participant mode not built

Status: Accepted (2026-10-18, delegated decision)

Decision: in live meetings the system surfaces clarifying questions privately to the human operator (terminal now, dashboard poll endpoint). It never posts into the meeting and never speaks. Participant mode (posting questions to meeting chat) is NOT implemented.

Why: the capture route is the operator's own machine and there is no Teams/meeting API integration (no M365 tenant); posting would need a bot identity, disclosure UX and tenant approval. Copilot delivers the value (ranked, deduplicated, pause-controlled questions) with a human as the speaker of record.

Revisit when: a Teams tenant exists and ADR 0002's Teams routes are taken up. A new ADR must define disclosure, consent and policy gates first.
