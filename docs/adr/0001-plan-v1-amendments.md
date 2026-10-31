# ADR 0001 — Plan v1 amendments to the initial architecture

Status: Accepted (user-authorized, 2026-10-18)

## Context
The initial `CLAUDE.md` froze a Teams-centric design with 7 agents. The user authorized modifying architecture and agents provided the agenda is met with full transparency.

## Decisions
1. **Capture is source-agnostic.** `MeetingSource` adapters R0–R4 (see `docs/plan.md` §3). Recommended real-meeting path: `teams_post_meeting` + own `local_capture` companion; `teams_live` bot and self-hosted room deferred behind spike gates.
2. **Live interaction modes:** Observer, Copilot (default; questions go privately to the human BA), Participant (opt-in, needs its own ADR).
3. **Agent roster expanded** from 7 to 17 modules, mostly deterministic: Consent & Privacy Guard, Injection Sentinel, Grounding Verifier, Stakeholder Authority Resolver, Conflict & Gap Detector, Decision Log Keeper, Testability Reviewer, Reconciler, Explainer, Eval & Replay Harness.
4. **Transparency contract T1–T10** adopted as testable requirements.
5. **Phase 5 (Hardening and pilot)** added.
6. **No new infrastructure:** the event bus is the Postgres job/event tables.

## Consequences
- Local Capture Companion is a new deployable (small Windows process) and a new consent surface.
- More modules to build; each still requires prompt + schema + tests (or deterministic unit tests).
- `CLAUDE.md` sections 6, 8, 14 updated to match.

## Unchanged
Retention policy, Jira safety rules, durable workflow rules, tech stack, "no Kafka/Redis/Celery" rule.
