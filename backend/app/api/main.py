import logging
import secrets
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.domain.requirement import ReqStatus, TransitionError
from app.integrations.jira.client import JiraClient
from app.integrations.jira.errors import JiraError
from app.integrations.jira.execution import build_executor
from app.integrations.jira.provision import JiraProvisionError, can_create_projects
from app.integrations.transcript_file.parser import TranscriptParseError
from app.logging import configure_logging
from app.orchestration.audit import append_audit, verify_client_chain
from app.persistence.db import make_session_factory
from app.persistence.models import (
    ActionIntent,
    Client,
    ConflictRecord,
    Job,
    Meeting,
    OpenQuestion,
    Project,
    Requirement,
    Stakeholder,
    User,
)
from app.services import auth, clients, delivery, flags, interview, jira_projects, knowledge, live
from app.services import conversation as conversation_svc
from app.services import meetings as meetings_svc
from app.services import overview as overview_svc
from app.services.capture_runner import CaptureBusyError, CaptureManager, CaptureParams
from app.services.explain import explain_requirement
from app.services.jira_projects import effective_allowlist
from app.services.minutes import render_minutes

COOKIE = "ba_session"
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_S = 300.0
SESSION_MAX_AGE_S = 8 * 3600


class LoginBody(BaseModel):
    email: str
    password: str


class ClientBody(BaseModel):
    name: str


def create_app(
    settings: Settings | None = None,
    factory: sessionmaker[Session] | None = None,
    capture_manager: CaptureManager | None = None,
    jira_client_factory: Callable[[], JiraClient] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    jira_factory = jira_client_factory or (lambda: build_executor(settings).client)
    captures = capture_manager or CaptureManager()
    factory = factory or make_session_factory()
    serializer = URLSafeTimedSerializer(
        settings.session_secret.get_secret_value(), salt="ba-session"
    )
    configure_logging()
    app = FastAPI(title="BA Platform", docs_url=None, redoc_url=None)
    if settings.session_secret.get_secret_value() == "dev-only-change-me":
        logging.getLogger(__name__).warning(
            "SESSION_SECRET is the development default; set a random value in .env"
        )
    failures: dict[str, list[float]] = {}

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        # FastAPI runs a dependency's cleanup AFTER the response is sent. Commit here instead, so
        # "201 Created" is only ever returned for data that is really saved, and a failed commit
        # is reported to the caller rather than lost.
        # The commit runs in a worker thread, NEVER on the event loop: a commit can flush writes
        # that wait on a row lock, and blocking the loop there froze the whole API (the request
        # holding the lock needed the loop to reach its own commit -> silent deadlock mid-call).
        sessions: list[Session] = getattr(request.state, "db_sessions", [])
        if response.status_code < 400 and sessions:
            try:
                for sess in sessions:
                    await run_in_threadpool(sess.commit)
            except Exception as exc:
                for sess in sessions:
                    await run_in_threadpool(sess.rollback)
                logging.getLogger(__name__).error("commit failed: %s", exc)
                return JSONResponse(
                    {"detail": "could not save the change; nothing was applied"}, 500
                )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def throttled(key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in failures.get(key, []) if now - t < LOGIN_WINDOW_S]
        failures[key] = recent
        return len(recent) >= LOGIN_MAX_FAILURES

    def db(request: Request) -> Iterator[Session]:
        session = factory()
        request.state.db_sessions = [*getattr(request.state, "db_sessions", []), session]
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    DB = Annotated[Session, Depends(db)]

    def current_session(request: Request) -> dict[str, str]:
        token = request.cookies.get(COOKIE)
        if not token:
            raise HTTPException(401, "not authenticated")
        try:
            data = serializer.loads(token, max_age=SESSION_MAX_AGE_S)
        except BadSignature as exc:
            raise HTTPException(401, "invalid session") from exc
        return dict(data)

    Session_ = Annotated[dict[str, str], Depends(current_session)]

    def require_csrf(sess: Session_, x_csrf_token: Annotated[str | None, Header()] = None) -> None:
        if not x_csrf_token or not secrets.compare_digest(x_csrf_token, sess["csrf"]):
            raise HTTPException(403, "missing or invalid CSRF token")

    CSRF = Depends(require_csrf)

    @app.get("/health")
    def health(session: DB) -> dict[str, object]:
        session.execute(text("SELECT 1"))
        return {"status": "ok"}  # liveness only; configuration is behind authentication (/control)

    @app.post("/auth/login")
    def login(body: LoginBody, response: Response, session: DB) -> dict[str, str]:
        key = body.email.lower().strip()
        if throttled(key):
            raise HTTPException(429, "too many failed logins; try again later")
        user = auth.authenticate(session, body.email, body.password)
        if user is None:
            failures.setdefault(key, []).append(time.monotonic())
            session.commit()  # keep the failed-login audit event
            raise HTTPException(401, "invalid credentials")
        failures.pop(key, None)
        csrf = secrets.token_urlsafe(32)
        token = serializer.dumps({"uid": str(user.id), "csrf": csrf})
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=settings.session_cookie_secure,
            max_age=SESSION_MAX_AGE_S,
        )
        return {"csrf_token": csrf}

    @app.post("/auth/logout", dependencies=[CSRF])
    def logout(response: Response) -> dict[str, str]:
        response.delete_cookie(COOKIE)
        return {"status": "logged_out"}

    @app.get("/clients")
    def get_clients(_: Session_, session: DB) -> list[dict[str, Any]]:
        """Clients with activity counts, most recently active first (the picker's default)."""
        return overview_svc.client_summaries(session)

    @app.get("/overview")
    def get_overview(_: Session_, session: DB) -> dict[str, Any]:
        """Everything across ALL clients: totals, status distribution, per-client table, recents."""
        return overview_svc.global_overview(session)

    @app.post("/clients", dependencies=[CSRF], status_code=201)
    def post_client(body: ClientBody, session: DB) -> dict[str, str]:
        c = clients.create_client(session, body.name)
        return {"id": str(c.id), "name": c.name}

    @app.get("/clients/{client_id}/jobs")
    def get_jobs(client_id: uuid.UUID, _: Session_, session: DB) -> list[dict[str, object]]:
        rows = session.scalars(
            select(Job).where(Job.client_id == client_id).order_by(Job.created_at.desc()).limit(200)
        )
        return [
            {
                "id": str(j.id),
                "kind": j.kind,
                "status": j.status,
                "attempts": j.attempts,
                "last_error": j.last_error,
            }
            for j in rows
        ]

    @app.get("/clients/{client_id}/audit/verify")
    def verify_audit(client_id: uuid.UUID, _: Session_, session: DB) -> dict[str, object]:
        bad = verify_client_chain(session, client_id)
        return {"valid": bad is None, "first_bad_index": bad}

    # ------------------------------------------------------------------ knowledge
    @app.get("/clients/{client_id}/requirements")
    def list_requirements(
        client_id: uuid.UUID, _: Session_, session: DB
    ) -> list[dict[str, object]]:
        rows = session.scalars(
            select(Requirement)
            .where(Requirement.client_id == client_id)
            .order_by(Requirement.created_at.desc())
            .limit(500)
        )
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "kind": r.kind,
                "status": r.status,
                "explicit": r.explicit,
                "version": r.current_version,
            }
            for r in rows
        ]

    @app.get("/clients/{client_id}/requirements/{requirement_id}")
    def requirement_trace(
        client_id: uuid.UUID, requirement_id: uuid.UUID, _: Session_, session: DB
    ) -> dict[str, Any]:
        try:
            return explain_requirement(session, client_id, requirement_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    class TransitionBody(BaseModel):
        target: str
        reason: str

    @app.post("/clients/{client_id}/requirements/{requirement_id}/transition", dependencies=[CSRF])
    def transition_req(
        client_id: uuid.UUID, requirement_id: uuid.UUID, body: TransitionBody, session: DB
    ) -> dict[str, str]:
        try:
            r = knowledge.transition_requirement(
                session, client_id, requirement_id, ReqStatus(body.target), body.reason
            )
        except TransitionError as exc:
            raise HTTPException(409, str(exc)) from exc  # guard refusal is surfaced, not hidden
        except (LookupError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"id": str(r.id), "status": r.status}

    @app.get("/clients/{client_id}/meetings")
    def list_meetings(client_id: uuid.UUID, _: Session_, session: DB) -> list[dict[str, object]]:
        live.mark_stale_degraded(session)
        return [
            {
                "id": str(m.id),
                "title": m.title,
                "status": m.status,
                "source": m.source,
                "consent": m.consent_status,
                "failure_reason": m.failure_reason,
                "transcript_purged": m.transcript_purged_at is not None,
                "degraded_reason": m.degraded_reason,
                "started_at": m.created_at.isoformat(),
                "mode": m.mode,
            }
            for m in meetings_svc.list_meetings(session, client_id)
        ]

    @app.get("/clients/{client_id}/questions")
    def list_questions(client_id: uuid.UUID, _: Session_, session: DB) -> list[dict[str, object]]:
        rows = session.scalars(
            select(OpenQuestion)
            .where(OpenQuestion.client_id == client_id)
            .order_by(OpenQuestion.blocking.desc(), OpenQuestion.created_at)
        )
        return [
            {
                "id": str(q.id),
                "question": q.question,
                "why": q.why_it_matters,
                "blocking": q.blocking,
                "status": q.status,
            }
            for q in rows
        ]

    @app.get("/clients/{client_id}/conflicts")
    def list_conflicts(client_id: uuid.UUID, _: Session_, session: DB) -> list[dict[str, object]]:
        rows = session.scalars(select(ConflictRecord).where(ConflictRecord.client_id == client_id))
        return [
            {
                "id": str(c.id),
                "relation": c.relation,
                "status": c.status,
                "similarity": round(c.similarity, 3),
                "explanation": c.explanation,
                "requirement_id": str(c.requirement_id),
                "other_requirement_id": str(c.other_requirement_id),
            }
            for c in rows
        ]

    class StakeholderBody(BaseModel):
        name: str
        role: str | None = None
        has_decision_authority: bool = False
        identity_verified: bool = False
        verification_note: str | None = None

    @app.post("/clients/{client_id}/stakeholders", dependencies=[CSRF], status_code=201)
    def post_stakeholder(
        client_id: uuid.UUID, body: StakeholderBody, session: DB
    ) -> dict[str, object]:
        sh = knowledge.upsert_stakeholder(
            session,
            client_id,
            body.name,
            body.role,
            body.has_decision_authority,
            body.identity_verified,
            body.verification_note,
        )
        return {"id": str(sh.id), "name": sh.name, "verified": sh.identity_verified}

    # ------------------------------------------------------------------ delivery
    @app.get("/clients/{client_id}/intents")
    def list_intents(client_id: uuid.UUID, _: Session_, session: DB) -> list[dict[str, object]]:
        rows = session.scalars(
            select(ActionIntent)
            .where(ActionIntent.client_id == client_id)
            .order_by(ActionIntent.created_at.desc())
            .limit(200)
        )
        return [
            {
                "id": str(i.id),
                "kind": i.kind,
                "status": i.status,
                "jira_key": i.jira_key,
                "project_key": i.project_key,
                "summary": i.payload.get("summary"),
                "requirement_id": str(i.requirement_id) if i.requirement_id else None,
                "last_error": i.last_error,
                "attempts": i.attempts,
                "marker": i.marker,
            }
            for i in rows
        ]

    class RejectBody(BaseModel):
        reason: str = ""

    def _actor(sess: dict[str, str], session: Session) -> str:
        user = session.get(User, uuid.UUID(sess["uid"]))
        return user.email if user else "unknown"

    @app.post("/clients/{client_id}/intents/{intent_id}/approve", dependencies=[CSRF])
    def approve(
        client_id: uuid.UUID, intent_id: uuid.UUID, sess: Session_, session: DB
    ) -> dict[str, str]:
        try:
            i = delivery.approve_intent(session, client_id, intent_id, _actor(sess, session))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except delivery.DeliveryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": str(i.id), "status": i.status}

    @app.post("/clients/{client_id}/intents/{intent_id}/reject", dependencies=[CSRF])
    def reject(
        client_id: uuid.UUID,
        intent_id: uuid.UUID,
        body: RejectBody,
        sess: Session_,
        session: DB,
    ) -> dict[str, str]:
        try:
            i = delivery.reject_intent(
                session, client_id, intent_id, _actor(sess, session), body.reason
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except delivery.DeliveryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": str(i.id), "status": i.status}

    # ------------------------------------------------------------------ live capture (Copilot)
    class LiveStartBody(BaseModel):
        title: str
        consent_basis: str
        project_id: uuid.UUID | None = None
        mode: str = "copilot"
        autopilot: bool = False
        remote_name: str | None = None
        remote_role: str | None = None
        remote_authority: bool = False

    class AgentSaidBody(BaseModel):
        text: str
        action: str = "ask"
        topic: str = "other"

    class LiveSegmentsBody(BaseModel):
        segments: list[live.LiveSegmentIn]

    class QuestionActionBody(BaseModel):
        action: str

    def _live_errors(fn: Any) -> Any:
        try:
            return fn()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except meetings_svc.ConsentRequiredError as exc:
            raise HTTPException(412, str(exc)) from exc
        except live.LiveError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/clients/{client_id}/live-meetings", dependencies=[CSRF], status_code=201)
    def live_start(
        client_id: uuid.UUID, body: LiveStartBody, sess: Session_, session: DB
    ) -> dict[str, str]:
        m = _live_errors(
            lambda: live.start_live_meeting(
                session,
                client_id,
                body.title,
                body.consent_basis,
                _actor(sess, session),
                body.project_id,
                None,
                body.mode,
                body.autopilot,
                body.remote_name,
                body.remote_role,
                body.remote_authority,
            )
        )
        return {"id": str(m.id), "status": m.status}

    @app.post("/clients/{client_id}/live-meetings/{meeting_id}/segments", dependencies=[CSRF])
    def live_segments(
        client_id: uuid.UUID, meeting_id: uuid.UUID, body: LiveSegmentsBody, session: DB
    ) -> dict[str, int]:
        n = _live_errors(lambda: live.add_segments(session, client_id, meeting_id, body.segments))
        return {"accepted": n}

    @app.post("/clients/{client_id}/live-meetings/{meeting_id}/heartbeat", dependencies=[CSRF])
    def live_heartbeat(client_id: uuid.UUID, meeting_id: uuid.UUID, session: DB) -> dict[str, str]:
        m = _live_errors(lambda: live.heartbeat(session, client_id, meeting_id))
        return {"status": m.status}

    @app.post("/clients/{client_id}/live-meetings/{meeting_id}/end", dependencies=[CSRF])
    def live_end(client_id: uuid.UUID, meeting_id: uuid.UUID, session: DB) -> dict[str, str]:
        m = _live_errors(lambda: live.end_meeting(session, client_id, meeting_id))
        return {"status": m.status, "failure_reason": m.failure_reason or ""}

    @app.get("/clients/{client_id}/live-meetings/{meeting_id}/suggestion")
    def live_suggestion(
        client_id: uuid.UUID, meeting_id: uuid.UUID, _: Session_, session: DB
    ) -> dict[str, Any]:
        s = _live_errors(lambda: live.suggest_now(session, client_id, meeting_id))
        q = s.question
        return {
            "reason": s.reason,
            "question": None
            if q is None
            else {
                "id": str(q.id),
                "text": q.question,
                "why": q.why_it_matters,
                "blocking": q.blocking,
            },
        }

    @app.get("/clients/{client_id}/live-meetings/{meeting_id}/next-utterance")
    def live_next_utterance(
        client_id: uuid.UUID, meeting_id: uuid.UUID, _: Session_, session: DB
    ) -> dict[str, Any]:
        out: dict[str, Any] = _live_errors(
            lambda: interview.next_utterance(session, client_id, meeting_id)
        )
        return out

    @app.post("/clients/{client_id}/live-meetings/{meeting_id}/agent-said", dependencies=[CSRF])
    def live_agent_said(
        client_id: uuid.UUID, meeting_id: uuid.UUID, body: AgentSaidBody, session: DB
    ) -> dict[str, str]:
        m = _live_errors(
            lambda: interview.record_agent_said(
                session, client_id, meeting_id, body.text, body.action, body.topic
            )
        )
        return {"status": m.status}

    @app.get("/clients/{client_id}/live-meetings/{meeting_id}/state")
    def live_state(
        client_id: uuid.UUID, meeting_id: uuid.UUID, _: Session_, session: DB
    ) -> dict[str, Any]:
        view: dict[str, Any] = _live_errors(
            lambda: interview.state_view(session, client_id, meeting_id)
        )
        return view

    @app.post("/clients/{client_id}/questions/{question_id}/action", dependencies=[CSRF])
    def question_action(
        client_id: uuid.UUID, question_id: uuid.UUID, body: QuestionActionBody, session: DB
    ) -> dict[str, str]:
        try:
            q = live.mark_question(session, client_id, question_id, body.action)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": str(q.id), "status": q.status}

    @app.get("/clients/{client_id}/meetings/{meeting_id}/autopilot")
    def autopilot_report(
        client_id: uuid.UUID, meeting_id: uuid.UUID, _: Session_, session: DB
    ) -> dict[str, Any]:
        m = session.get(Meeting, meeting_id)
        if m is None or m.client_id != client_id:
            raise HTTPException(404, "meeting not found in client scope")
        report: dict[str, Any] = (m.interview_state or {}).get("autopilot_report") or {}
        return {"autopilot": m.autopilot, "status": m.status, "report": report}

    # ------------------------------------------------------------------ conversations
    @app.get("/clients/{client_id}/conversations")
    def list_conversations(
        client_id: uuid.UUID, _: Session_, session: DB
    ) -> list[dict[str, object]]:
        live.mark_stale_degraded(session)
        return conversation_svc.list_conversations(session, client_id)

    @app.get("/clients/{client_id}/meetings/{meeting_id}/conversation")
    def get_conversation(
        client_id: uuid.UUID, meeting_id: uuid.UUID, _: Session_, session: DB, after: int = -1
    ) -> dict[str, Any]:
        try:
            return conversation_svc.get_conversation(session, client_id, meeting_id, after)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ------------------------------------------------------------------ minutes + corrections
    class CorrectionBody(BaseModel):
        title: str
        statement: str
        reason: str

    @app.get("/clients/{client_id}/meetings/{meeting_id}/minutes")
    def get_minutes(
        client_id: uuid.UUID, meeting_id: uuid.UUID, _: Session_, session: DB
    ) -> dict[str, str]:
        try:
            return {"markdown": render_minutes(session, client_id, meeting_id)}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/clients/{client_id}/requirements/{requirement_id}/correct", dependencies=[CSRF])
    def correct_requirement(
        client_id: uuid.UUID,
        requirement_id: uuid.UUID,
        body: CorrectionBody,
        sess: Session_,
        session: DB,
    ) -> dict[str, object]:
        try:
            r = knowledge.apply_correction(
                session,
                client_id,
                requirement_id,
                body.title,
                body.statement,
                body.reason,
                _actor(sess, session),
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TransitionError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": str(r.id), "status": r.status, "version": r.current_version}

    # ------------------------------------------------------------------ setup screens
    @app.get("/clients/{client_id}/stakeholders")
    def list_stakeholders(
        client_id: uuid.UUID, _: Session_, session: DB
    ) -> list[dict[str, object]]:
        rows = session.scalars(
            select(Stakeholder).where(Stakeholder.client_id == client_id).order_by(Stakeholder.name)
        )
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "role": s.role,
                "has_decision_authority": s.has_decision_authority,
                "identity_verified": s.identity_verified,
                "verification_note": s.verification_note,
            }
            for s in rows
        ]

    class ProjectBody(BaseModel):
        name: str
        jira_project_key: str | None = None

    @app.get("/clients/{client_id}/projects")
    def get_projects(client_id: uuid.UUID, _: Session_, session: DB) -> list[dict[str, object]]:
        return [
            {
                "id": str(p.id),
                "name": p.name,
                "jira_project_key": p.jira_project_key,
                "jira_provisioned": p.jira_provisioned,
            }
            for p in clients.list_projects(session, client_id)
        ]

    @app.post("/clients/{client_id}/projects", dependencies=[CSRF], status_code=201)
    def post_project(client_id: uuid.UUID, body: ProjectBody, session: DB) -> dict[str, object]:
        try:
            p = clients.create_project(
                session,
                client_id,
                body.name,
                body.jira_project_key or None,
                effective_allowlist(session, settings),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": str(p.id), "name": p.name, "jira_project_key": p.jira_project_key}

    class ProvisionBody(BaseModel):
        name: str
        jira_key: str | None = None
        create_in_jira: bool = True

    @app.get("/jira/capabilities")
    async def jira_capabilities(_: Session_) -> dict[str, Any]:
        """Can this deployment create Jira projects? (read-only permission check)"""
        configured = bool(settings.jira_base_url and settings.jira_email)
        if not configured:
            return {
                "configured": False,
                "can_create_projects": False,
                "reason": "Jira is not configured",
            }
        client = jira_factory()
        try:
            ok, why = await can_create_projects(client)
        finally:
            await client.aclose()
        return {"configured": True, "can_create_projects": ok, "reason": why}

    @app.post("/clients/{client_id}/projects/provision", dependencies=[CSRF], status_code=201)
    async def provision(client_id: uuid.UUID, body: ProvisionBody, session: DB) -> dict[str, Any]:
        client = jira_factory()
        try:
            p = await jira_projects.provision_for_client(
                session,
                settings,
                client,
                client_id,
                body.name,
                body.jira_key,
                body.create_in_jira,
            )
        except (jira_projects.ProvisionRefused, JiraProvisionError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except JiraError as exc:
            raise HTTPException(502, f"Jira refused or failed: {exc}") from exc
        finally:
            await client.aclose()
        return {
            "id": str(p.id),
            "name": p.name,
            "jira_project_key": p.jira_project_key,
            "jira_provisioned": p.jira_provisioned,
        }

    MAX_UPLOAD = 5 * 1024 * 1024

    @app.post("/clients/{client_id}/meetings/import", dependencies=[CSRF], status_code=201)
    async def import_transcript_upload(
        client_id: uuid.UUID,
        sess: Session_,
        session: DB,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()],
        consent_basis: Annotated[str, Form()],
        project_id: Annotated[str | None, Form()] = None,
    ) -> dict[str, str]:
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "transcript file is larger than 5 MB")
        suffix = Path(file.filename or "").suffix.lower()
        with tempfile.TemporaryDirectory(prefix="ba-upload-") as tmp:
            path = Path(tmp) / f"upload{suffix}"
            path.write_bytes(data)
            try:
                m = meetings_svc.import_transcript(
                    session,
                    client_id,
                    path,
                    title,
                    consent_basis,
                    _actor(sess, session),
                    uuid.UUID(project_id) if project_id else None,
                )
            except meetings_svc.ConsentRequiredError as exc:
                raise HTTPException(412, str(exc)) from exc
            except TranscriptParseError as exc:
                raise HTTPException(422, str(exc)) from exc
        return {"id": str(m.id), "status": m.status}

    # ------------------------------------------------------------------ live capture from the UI
    def _probe(*args: str, timeout: float = 45.0) -> dict[str, Any]:
        """Run an audio probe in its own process (Windows audio needs a COM-initialised thread)."""
        import json as _json
        import subprocess as _sp
        import sys as _sys

        try:
            r = _sp.run(
                [_sys.executable, "-m", "app.capture.probe", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=_sp.DEVNULL,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
            )
            lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
            data: dict[str, Any] = _json.loads(lines[-1]) if lines else {}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        if not data:
            return {"error": (r.stderr or "probe produced no output")[-300:]}
        return data

    options_cache: dict[str, Any] = {"at": 0.0, "value": None}

    @app.get("/capture/options")
    def capture_options(_: Session_, refresh: bool = False) -> dict[str, Any]:
        """What this machine offers for the form: audio devices and voices (never fails the page)."""
        import time as _time

        now = _time.monotonic()
        if not refresh and options_cache["value"] and now - options_cache["at"] < 60:
            cached: dict[str, Any] = options_cache["value"]
            return cached
        data = _probe("options")
        if "error" in data:
            data = {
                "available": False,
                "reason": data["error"],
                "microphones": [],
                "loopback": [],
                "speakers": [],
                "voices": [],
                "default_speaker": None,
                "cable_found": False,
            }
        if data.get("available"):
            options_cache.update(at=now, value=data)
        return data

    @app.get("/capture/status")
    def capture_status(_: Session_) -> dict[str, Any]:
        return captures.status()

    @app.post("/capture/start", dependencies=[CSRF], status_code=201)
    def capture_start(
        body: CaptureParams, request: Request, sess: Session_, session: DB
    ) -> dict[str, str]:
        client = session.get(Client, body.client_id)
        if client is None:
            raise HTTPException(404, "client not found")
        if body.project_id is not None:
            project = session.get(Project, body.project_id)
            if project is None or project.client_id != body.client_id:
                raise HTTPException(404, "project not found for this client")
        cookie = serializer.dumps({"uid": sess["uid"], "csrf": sess["csrf"]})
        try:
            run_id = captures.start(
                body, settings.api_base_url, cookie, sess["csrf"], _actor(sess, session)
            )
        except CaptureBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        append_audit(
            session,
            "capture.started_from_dashboard",
            {"run_id": run_id, "mode": body.mode, "autopilot": body.autopilot, "title": body.title},
            body.client_id,
        )
        return {"run_id": run_id}

    @app.post("/capture/stop", dependencies=[CSRF])
    def capture_stop(_: Session_) -> dict[str, bool]:
        return {"stopping": captures.stop()}

    class TestVoiceBody(BaseModel):
        device: str = ""
        voice: str = ""

    def _audio_idle() -> None:
        if captures.status()["state"] in {"running", "stopping"}:
            raise HTTPException(409, "a call is being captured; test devices after it ends")

    @app.post("/capture/test-voice", dependencies=[CSRF])
    def capture_test_voice(body: TestVoiceBody, _: Session_) -> dict[str, object]:
        """Speak a short line on the chosen output device (what Teams would hear on a cable)."""
        _audio_idle()
        if body.device.startswith("-") or body.voice.startswith("-"):
            raise HTTPException(422, "invalid device or voice")
        data = _probe("test-voice", body.device, body.voice, timeout=60.0)
        if "error" in data:
            raise HTTPException(422, f"voice test failed: {data['error']}")
        return {"ok": True}

    class TestAudioBody(BaseModel):
        device: str = ""

    @app.post("/capture/test-audio", dependencies=[CSRF])
    def capture_test_audio(body: TestAudioBody, _: Session_) -> dict[str, object]:
        """Listen for 2 seconds to what the machine plays: proves Teams audio reaches the agent."""
        _audio_idle()
        if body.device.startswith("-"):
            raise HTTPException(422, "invalid device")
        data = _probe("test-audio", body.device)
        if "error" in data:
            raise HTTPException(422, f"audio test failed: {data['error']}")
        return {"peak": data.get("peak", 0), "hearing_sound": bool(data.get("hearing_sound"))}

    # ------------------------------------------------------------------ controls
    class KillBody(BaseModel):
        on: bool

    @app.get("/control")
    def get_control(_: Session_, session: DB) -> dict[str, object]:
        return {
            "kill_switch": flags.kill_switch_on(session, settings),
            "autonomy_mode": flags.autonomy_mode(session, settings).value,
            "jira_base_url": settings.jira_base_url,
            "jira_allowlist": sorted(effective_allowlist(session, settings)),
        }

    @app.post("/control/kill-switch", dependencies=[CSRF])
    def set_kill(body: KillBody, sess: Session_, session: DB) -> dict[str, object]:
        flags.set_kill_switch(session, body.on, _actor(sess, session))
        return {"kill_switch": body.on}

    return app
