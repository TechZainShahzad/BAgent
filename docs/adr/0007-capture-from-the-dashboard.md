# ADR 0007: Live capture is started and stopped from the dashboard

Status: Accepted (user request, 2026-10-18)

## Decision
The dashboard's **Live capture** screen replaces typing `ba capture ...`. The API process launches the capture program as a child process on the same machine (it needs this machine's audio devices), using an argument list (no shell). The child reuses the dashboard user's session (cookie + CSRF passed through its environment, never on the command line), logs to a file the API tails, and ends gracefully when a stop file appears. One capture at a time. The CLI command still works.

## Safeguards
Authenticated and CSRF-protected endpoints; every field validated (no value may start with `-`, autopilot requires interview mode + a named confirmable speaker); client and project must match; the start is audited per client; device/voice probes run in a separate short-lived process and are refused while a call is running; consent text is mandatory.

## Consequences
Anyone logged in to the dashboard can start recording on the host machine. The dashboard binds to 127.0.0.1 and is single-operator by design; do not expose it to a network without adding roles first.
