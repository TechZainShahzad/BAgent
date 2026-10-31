from pathlib import Path
from typing import Any

import typer

from app.config import get_settings
from app.orchestration.audit import verify_client_chain
from app.persistence.db import unit_of_work
from app.services import auth, clients
from app.services.jira_projects import effective_allowlist

app = typer.Typer(no_args_is_help=True, help="BA platform CLI (same services as the API).")


@app.command("create-user")
def create_user(
    email: str, password: str = typer.Option(..., prompt=True, hide_input=True)
) -> None:
    with unit_of_work() as s:
        user = auth.create_user(s, email, password)
        typer.echo(f"created user {user.email}")


@app.command("list-users")
def list_users() -> None:
    """List dashboard users (emails only)."""
    with unit_of_work() as s:
        for u in auth.list_users(s):
            typer.echo(f"{u.email}  active={u.is_active}  created={u.created_at:%Y-%m-%d}")


@app.command("set-password")
def set_password(
    email: str,
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Reset a dashboard user's password."""
    try:
        with unit_of_work() as s:
            auth.set_password(s, email, password)
    except (ValueError, LookupError) as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"password updated for {email}")


@app.command("create-client")
def create_client(name: str) -> None:
    with unit_of_work() as s:
        c = clients.create_client(s, name)
        typer.echo(f"{c.id} {c.name}")


@app.command("list-clients")
def list_clients() -> None:
    with unit_of_work() as s:
        for c in clients.list_clients(s):
            typer.echo(f"{c.id} {c.name}")


@app.command("list-projects")
def list_projects(client_id: str) -> None:
    """List a client's projects with their ids (the id is what --project-id needs)."""
    import uuid

    with unit_of_work() as s:
        for p in clients.list_projects(s, uuid.UUID(client_id)):
            typer.echo(f"{p.id} {p.name} jira={p.jira_project_key}")


@app.command("create-project")
def create_project(client_id: str, name: str, jira_key: str = "") -> None:
    import uuid

    with unit_of_work() as s:
        p = clients.create_project(
            s,
            uuid.UUID(client_id),
            name,
            jira_key or None,
            effective_allowlist(s, get_settings()),
        )
        typer.echo(f"{p.id} {p.name} jira={p.jira_project_key}")


@app.command("verify-audit")
def verify_audit(client_id: str = "") -> None:
    import uuid

    with unit_of_work() as s:
        bad = verify_client_chain(s, uuid.UUID(client_id) if client_id else None)
    if bad is None:
        typer.echo("audit chain OK")
    else:
        typer.echo(f"AUDIT CHAIN BROKEN at index {bad}", err=True)
        raise typer.Exit(1)


@app.command("import-transcript")
def import_transcript(
    client_id: str,
    path: str,
    title: str,
    consent_basis: str = typer.Option(..., help="Who attested consent and on what basis"),
    recorded_by: str = typer.Option("cli-operator"),
    project_id: str = "",
) -> None:
    import uuid
    from pathlib import Path

    from app.services import meetings

    with unit_of_work() as s:
        m = meetings.import_transcript(
            s,
            uuid.UUID(client_id),
            Path(path),
            title,
            consent_basis,
            recorded_by,
            uuid.UUID(project_id) if project_id else None,
        )
        typer.echo(f"meeting {m.id} queued for analysis")


@app.command("list-requirements")
def list_requirements(client_id: str) -> None:
    import uuid

    from sqlalchemy import select

    from app.persistence.models import Requirement

    with unit_of_work() as s:
        for r in s.scalars(
            select(Requirement).where(Requirement.client_id == uuid.UUID(client_id))
        ):
            typer.echo(f"{r.id} [{r.status}] ({r.kind}) {r.title}")


@app.command("worker")
def worker(
    once: bool = typer.Option(False, help="Process the queue until empty, then exit"),
) -> None:
    import asyncio

    from app.orchestration.runner import JobRunner
    from app.persistence.db import make_session_factory
    from app.providers.embeddings import build_embedding_provider
    from app.providers.llm import build_llm_provider
    from app.services.handlers import build_handlers
    from app.services.llm_logging import LoggedLLMProvider

    settings = get_settings()
    factory = make_session_factory()
    provider = LoggedLLMProvider(build_llm_provider(settings), factory)
    embedder = build_embedding_provider(settings)
    runner = JobRunner(factory, handlers=build_handlers(settings, provider, embedder))
    typer.echo(f"worker started (llm_provider={settings.llm_provider})")
    if once:
        runner.recover()
        typer.echo(f"processed {asyncio.run(runner.run_until_empty())} job(s)")
    else:
        import time as _time

        from app.services.live import mark_stale_degraded

        last_tick = [0.0]

        def tick(session: Any) -> None:
            from app.services import flags, knowledge

            if _time.monotonic() - last_tick[0] < 2.0:  # the loop polls fast; housekeeping does not
                return
            last_tick[0] = _time.monotonic()
            mark_stale_degraded(session)
            if flags.purge_due(session):
                knowledge.purge_expired(session, grace_hours=settings.transcript_grace_hours)

        import threading

        from app.services.interview import WARM_JOB
        from app.services.live import PLAN_JOB

        # Two lanes: live replies (plan/warm) must never wait behind a minutes-long analysis.
        # Each lane runs in its OWN thread + event loop + provider. DB calls are synchronous, so
        # with one shared loop a live job blocked on a row lock froze the loop, the batch job
        # holding that lock could never resume to commit, and the worker deadlocked silently.
        live_kinds = {PLAN_JOB, WARM_JOB}
        live_provider = LoggedLLMProvider(build_llm_provider(settings), factory)
        live_handlers = build_handlers(settings, live_provider, embedder)
        batch_handlers = build_handlers(settings, provider, embedder)
        live_runner = JobRunner(
            factory, handlers={k: h for k, h in live_handlers.items() if k in live_kinds}
        )
        batch_runner = JobRunner(
            factory, handlers={k: h for k, h in batch_handlers.items() if k not in live_kinds}
        )
        crashed: list[BaseException] = []

        def lane(name: str, coro_fn: Any) -> threading.Thread:
            def run() -> None:
                try:
                    asyncio.run(coro_fn())
                except BaseException as exc:  # surface, never die silently
                    crashed.append(exc)
                    typer.echo(f"worker lane '{name}' crashed: {type(exc).__name__}: {exc}")

            t = threading.Thread(target=run, name=f"lane-{name}", daemon=True)
            t.start()
            return t

        threads = [
            lane("live", lambda: live_runner.run_forever(poll_s=0.2)),
            lane("batch", lambda: batch_runner.run_forever(poll_s=1.0, tick=tick)),
        ]
        while all(t.is_alive() for t in threads):
            _time.sleep(1.0)
        raise typer.Exit(1)  # one lane died: exit loudly instead of running half a worker


jira_app = typer.Typer(no_args_is_help=True, help="Jira workflow commands.")
app.add_typer(jira_app, name="jira")


@jira_app.command("discover")
def jira_discover(client_id: str, project_key: str) -> None:
    """Discover and cache the Jira project schema (allowlisted projects only)."""
    import asyncio
    import uuid

    from app.integrations.jira.discovery import discover_project
    from app.integrations.jira.execution import build_executor
    from app.orchestration.audit import append_audit
    from app.persistence.models import JiraSchemaSnapshot

    settings = get_settings()

    async def go() -> dict[str, Any]:
        ex = build_executor(settings)
        try:
            with unit_of_work() as s:
                allowed = effective_allowlist(s, settings)
            return await discover_project(ex.client, project_key, allowed)
        finally:
            await ex.client.aclose()

    snap = asyncio.run(go())
    with unit_of_work() as s:
        s.add(
            JiraSchemaSnapshot(
                client_id=uuid.UUID(client_id), project_key=project_key, payload=snap
            )
        )
        append_audit(s, "jira.schema_discovered", {"project": project_key}, uuid.UUID(client_id))
    for t in snap["issue_types"]:
        typer.echo(f"{t['name']:8} level={t['hierarchy_level']} required={t['required_fields']}")


@jira_app.command("draft")
def jira_draft(client_id: str, requirement_id: str, project_key: str) -> None:
    """Draft a story for a requirement (LLM) and run the testability review."""
    import asyncio
    import uuid

    from app.providers.llm import build_llm_provider
    from app.services import delivery

    settings = get_settings()
    from app.persistence.db import make_session_factory
    from app.services.llm_logging import LoggedLLMProvider

    provider = LoggedLLMProvider(build_llm_provider(settings), make_session_factory())

    async def go() -> None:
        with unit_of_work() as s:
            d = await delivery.draft_story(
                s, provider, uuid.UUID(client_id), uuid.UUID(requirement_id), project_key
            )
            ready = await delivery.make_ready_if_measurable(
                s, uuid.UUID(client_id), uuid.UUID(requirement_id)
            )
            typer.echo(f"[{d.issue_type}] {d.summary}")
            for ac in d.acceptance_criteria:
                typer.echo(f"  - {ac}")
            typer.echo(f"measurable={d.ac_measurable} ready_for_delivery={ready}")
            for f in d.review_flags:
                typer.echo(f"  flag: {f}")

    asyncio.run(go())


@jira_app.command("deliver")
def jira_deliver(
    client_id: str, requirement_id: str, project_key: str, by: str = "cli-operator"
) -> None:
    """Evaluate policy and create an ActionIntent per the current autonomy mode."""
    import asyncio
    import uuid

    from app.integrations.jira.execution import build_executor
    from app.services import delivery

    settings = get_settings()

    async def go() -> None:
        ex = build_executor(settings)
        try:
            with unit_of_work() as s:
                p = await delivery.prepare_delivery(
                    s,
                    settings,
                    ex,
                    uuid.UUID(client_id),
                    uuid.UUID(requirement_id),
                    project_key,
                    by,
                )
                typer.echo(
                    f"policy: {p.decision.outcome} {p.decision.rule_ids} -> {p.disposition.value}"
                )
                typer.echo(f"reason: {p.reasons[0]}")
                if p.intent:
                    typer.echo(
                        f"intent {p.intent.id} kind={p.intent.kind} status={p.intent.status}"
                    )
        finally:
            await ex.client.aclose()

    asyncio.run(go())


@jira_app.command("intents")
def jira_intents(client_id: str) -> None:
    import uuid

    from sqlalchemy import select

    from app.persistence.models import ActionIntent

    with unit_of_work() as s:
        rows = s.scalars(
            select(ActionIntent)
            .where(ActionIntent.client_id == uuid.UUID(client_id))
            .order_by(ActionIntent.created_at.desc())
        )
        for i in rows:
            typer.echo(
                f"{i.id} {i.kind:13} {i.status:18} {i.jira_key or '-':10} {i.last_error or ''}"
            )


@jira_app.command("approve")
def jira_approve(client_id: str, intent_id: str, by: str = "cli-operator") -> None:
    import uuid

    from app.services import delivery

    with unit_of_work() as s:
        i = delivery.approve_intent(s, uuid.UUID(client_id), uuid.UUID(intent_id), by)
        typer.echo(f"approved {i.id}; run `ba worker --once` to execute")


@jira_app.command("reject")
def jira_reject(client_id: str, intent_id: str, reason: str, by: str = "cli-operator") -> None:
    import uuid

    from app.services import delivery

    with unit_of_work() as s:
        delivery.reject_intent(s, uuid.UUID(client_id), uuid.UUID(intent_id), by, reason)
        typer.echo("rejected")


@app.command("kill-switch")
def kill_switch(state: str, by: str = "cli-operator") -> None:
    """Pause (on) or resume (off) all external writes."""
    from app.services import flags

    if state not in {"on", "off"}:
        raise typer.BadParameter("state must be on or off")
    with unit_of_work() as s:
        flags.set_kill_switch(s, state == "on", by)
    typer.echo(f"kill switch {state}")


@app.command("set-stakeholder")
def set_stakeholder(
    client_id: str,
    name: str,
    role: str = "",
    authority: bool = False,
    verified: bool = False,
    note: str = "",
) -> None:
    """Record a stakeholder. Verification is a human decision, never inferred."""
    import uuid

    from app.services import knowledge

    with unit_of_work() as s:
        sh = knowledge.upsert_stakeholder(
            s, uuid.UUID(client_id), name, role or None, authority, verified, note or None
        )
        typer.echo(
            f"{sh.id} {sh.name} authority={sh.has_decision_authority} verified={sh.identity_verified}"
        )


@app.command("transition")
def transition(client_id: str, requirement_id: str, target: str, reason: str) -> None:
    """Move a requirement through its lifecycle (guards enforced)."""
    import uuid

    from app.domain.requirement import ReqStatus, TransitionError
    from app.services import knowledge

    try:
        with unit_of_work() as s:
            r = knowledge.transition_requirement(
                s, uuid.UUID(client_id), uuid.UUID(requirement_id), ReqStatus(target), reason
            )
            typer.echo(f"{r.id} -> {r.status}")
    except TransitionError as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(1) from exc


BANNER = """
==================== RECORDING / TRANSCRIBING ====================
An AI business-analyst assistant is capturing this meeting's audio
ON THIS MACHINE and transcribing it locally. Raw audio is NOT saved.
Only transcript text is sent to the local BA platform.
Every participant must have been informed and must have consented.
Consent basis: {basis}
Press Ctrl-C to stop and run the final analysis.
==================================================================
"""


def show_suggestion(q: dict[str, Any]) -> None:
    flag = " [BLOCKING]" if q.get("blocking") else ""
    typer.echo("")
    typer.echo(f">>> SUGGESTED QUESTION{flag}: {q['text']}")
    typer.echo(f"    why: {q['why']}  (id {q['id']})")


@app.command("capture")
def capture(
    client_id: str,
    title: str,
    consent_basis: str = typer.Option(..., help="Who attested participant consent, and how"),
    email: str = typer.Option("", envvar="BA_EMAIL", help="Dashboard login (asked if missing)"),
    password: str = typer.Option("", envvar="BA_PASSWORD", help="Asked if missing"),
    api: str = typer.Option("http://127.0.0.1:8000", envvar="BA_API"),
    session_cookie: str = typer.Option(
        "", envvar="BA_SESSION_COOKIE", help="Reuse a dashboard session (set by the dashboard)"
    ),
    csrf: str = typer.Option("", envvar="BA_CSRF", help="CSRF token that goes with the session"),
    stop_file: str = typer.Option("", help="End the call gracefully when this file appears"),
    mic: bool = typer.Option(True, help="Capture the operator microphone"),
    loopback: bool = typer.Option(True, help="Capture system audio (remote participants)"),
    mic_device: str = "",
    loopback_device: str = "",
    model: str = "small.en",
    duration: float = typer.Option(0.0, help="Stop after N seconds (0 = until Ctrl-C)"),
    interview: bool = typer.Option(False, help="Interview mode: the agent speaks questions aloud"),
    autopilot: bool = typer.Option(
        False, help="After the meeting, push confirmed work to Jira alone"
    ),
    remote_name: str = typer.Option("", help="Declare who the remote voice is (operator-asserted)"),
    remote_role: str = "",
    remote_authority: bool = typer.Option(False, help="Declared remote speaker may confirm scope"),
    agent_output_device: str = typer.Option("", help="e.g. 'CABLE Input' to speak into Teams"),
    agent_voice: str = typer.Option("", help="Windows voice name (see --list-voices)"),
    list_voice_names: bool = typer.Option(False, "--list-voices", help="List TTS voices and exit"),
    project_id: str = "",
    list_audio: bool = typer.Option(False, "--list-devices", help="List audio devices and exit"),
) -> None:
    """Live capture (Copilot mode): local STT, questions shown privately to YOU only."""
    from app.capture import audio
    from app.capture.client import CaptureApiError, CaptureClient
    from app.capture.session import CaptureSession
    from app.capture.stt import FasterWhisperProvider

    if list_voice_names:
        from app.capture.voice import list_voices

        typer.echo(list_voices())
        return
    if list_audio:
        for kind, names in audio.list_devices().items():
            typer.echo(f"{kind}: {names}")
        return
    cc = CaptureClient(api)
    try:
        if session_cookie:
            cc.use_session(session_cookie, csrf)  # started from the dashboard: no password needed
        else:
            if not email:
                email = typer.prompt("Dashboard login email")
            if not password:
                password = typer.prompt("Password", hide_input=True)
            cc.login(email, password)
        extra: dict[str, Any] = {
            "mode": "interview" if interview else "copilot",
            "autopilot": autopilot,
            "remote_name": remote_name or None,
            "remote_role": remote_role or None,
            "remote_authority": remote_authority,
            "project_id": project_id or None,
        }
        meeting_id = cc.start(client_id, title, consent_basis, extra)
    except CaptureApiError as exc:
        typer.echo(f"cannot start: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"MEETING_ID {meeting_id}")
    typer.echo(BANNER.format(basis=consent_basis))
    stt = FasterWhisperProvider(model)
    typer.echo("loading speech model...")
    stt._load()
    typer.echo(f"speech model ready on {stt.stats.device} {stt.stats.notes or ''}")
    voice = None
    if interview:
        from app.capture.voice import VoiceOutput

        voice = VoiceOutput(agent_output_device or None, agent_voice or None)
    session = CaptureSession(
        cc,
        client_id,
        meeting_id,
        stt,
        on_suggestion=show_suggestion,
        on_status=lambda m: typer.echo(f"[status] {m}", err=True),
        voice=voice,
        # speaking into the default speakers would be heard by our own loopback: mute while talking
        mute_during_speech=not agent_output_device,
    )
    if mic:
        session.add_source("mic", audio.mic_frames(mic_device or None))
    if loopback:
        session.add_source("loopback", audio.loopback_frames(loopback_device or None))
    try:
        stop_path = Path(stop_file) if stop_file else None
        session.run(
            duration_s=duration or None,
            should_stop=(lambda: stop_path.exists()) if stop_path else None,
        )
    except KeyboardInterrupt:
        session.stop()
        session._shutdown()
    if session.end_requested:
        typer.echo("the agent finished the interview; ending the meeting")
    elif stop_file and Path(stop_file).exists():
        typer.echo("stop requested from the dashboard; ending the meeting")
    result = cc.end(client_id, meeting_id)
    typer.echo(f"meeting ended: {result}; sent {session.sent} segment(s); errors: {session.errors}")
    typer.echo(f"STT stats: {stt.stats}")
    typer.echo(
        "Run `ba worker --once` (LLM_PROVIDER=claude_cli) to analyze, then review in the dashboard."
    )


@app.command("minutes")
def minutes(client_id: str, meeting_id: str, out: str = "") -> None:
    """Render draft minutes (markdown) for attendees to review and correct."""
    import uuid
    from pathlib import Path

    from app.services.minutes import render_minutes

    with unit_of_work() as s:
        text = render_minutes(s, uuid.UUID(client_id), uuid.UUID(meeting_id))
    if out:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"written to {out}")
    else:
        typer.echo(text)


@app.command("purge")
def purge() -> None:
    """Run the retention purge now (expired evidence/chunks, orphaned transcript segments)."""
    from app.services import knowledge

    with unit_of_work() as s:
        run = knowledge.purge_expired(s, grace_hours=get_settings().transcript_grace_hours)
        typer.echo(f"purged evidence={run.evidence_deleted} segments={run.segments_deleted}")


if __name__ == "__main__":
    app()
