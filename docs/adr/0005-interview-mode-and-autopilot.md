# ADR 0005 — Interview mode (agent speaks) and autopilot; supersedes ADR 0004

Status: Accepted (user decision, 2026-10-18: "our agents don't need human approval, just ask question and gather requirements as an independent BA")

## Decisions
1. **Interview mode.** On an operator-owned capture the agent may speak. Server code decides WHEN (client pause >= 2.2 s, cooldown, fresh plan only); the LLM only proposes WHAT (one plain-language question or a confirm-back). It always opens by stating it is an AI, that it transcribes locally and saves no audio.
2. **Confirm-back as evidence.** The client's clear "yes" to an assistant paraphrase becomes evidence for that paraphrase only if it immediately follows it and passes a deterministic affirmative check. Assistant text alone is never evidence.
3. **Declared remote speaker.** Voice identity cannot be proven. The operator declares who the remote voice is (and whether they may confirm scope). Recorded as `operator_declared`, never as voice-verified.
4. **Autopilot.** With `--autopilot`, after the final analysis the system confirms what the declared sponsor said (all lifecycle guards still apply), creates the Epic for the goal, then Stories linked under it, with no human approval. Constraints, NFRs and success measures go into the Epic description with the client's quotes. Anything not testable, conflicting, or duplicated is HELD and listed in the autopilot report.
5. **Technical BA content** (data/roles/integration/security suggestions, assumptions) is included in issues under a separate heading "assistant-suggested, not stated by the client".

## Safeguards kept without human approval
Kill switch (re-checked at execution), project allowlist (SCRUM), deterministic policy engine, grounding verification, idempotent intents with reconcile-before-retry, audit chain, spoken AI disclosure, consent record, conflicts never auto-resolved, blocking questions block only the requirements they name.

## Consequences
Autopilot writes to Jira unattended in `autonomous` mode; use only on sandbox projects until the pilot sign-off. Spoken output into Teams needs a virtual audio cable (new native component, requires owner approval).
