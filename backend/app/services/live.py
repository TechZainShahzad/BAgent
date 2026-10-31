"""Live meeting sessions (Copilot mode).

- Segments stream in from the local capture companion; they are persisted (short-lived) and
  purged after the final analysis like any transcript.
- Questions go PRIVATELY to the human operator, never into the meeting. They are pause
  controlled (code decides, not the model) and rate-limited.
- If the stream goes quiet the meeting is marked degraded, questioning stops, and the dashboard
  shows it. Nothing is hidden.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.agents.requirements_analyst import SegmentIn, analyze
from app.config import Settings
from app.orchestration import jobs
from app.orchestration.audit import append_audit
from app.orchestration.jobs import Handler
from app.persistence.models import (
    ConsentRecord,
    Job,
    Meeting,
    OpenQuestion,
    TranscriptSegment,
)
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.services import conversation, elicitation
from app.services.meetings import ANALYZE_JOB, ConsentRequiredError

WINDOW_JOB = "meeting.live_window"
PLAN_JOB = "interview.plan"
WINDOW_EVERY_N_SEGMENTS = 8
WINDOW_SIZE = 40
HEARTBEAT_STALE_S = 20.0
PAUSE_S = 3.0
QUESTION_COOLDOWN_S = 30.0
LIVE_STATES = {"live", "degraded"}


class LiveSegmentIn(BaseModel):
    text: str
    start_s: float | None = None
    end_s: float | None = None
    channel: str | None = None  # mic | loopback
    speaker: str | None = None  # only what the source states; never invented


class LiveError(Exception):
    """Operation not valid for the meeting's current state."""


@dataclass(frozen=True)
class Suggestion:
    question: OpenQuestion | None
    reason: str  # why nothing was suggested, or "ready"


def _utc(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _live_meeting(session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID) -> Meeting:
    m = session.get(Meeting, meeting_id)
    if m is None or m.client_id != client_id:
        raise LookupError("meeting not found in client scope")
    return m


def start_live_meeting(
    session: Session,
    client_id: uuid.UUID,
    title: str,
    consent_basis: str,
    recorded_by: str,
    project_id: uuid.UUID | None = None,
    now: datetime | None = None,
    mode: str = "copilot",
    autopilot: bool = False,
    remote_name: str | None = None,
    remote_role: str | None = None,
    remote_authority: bool = False,
) -> Meeting:
    if not consent_basis.strip():
        raise ConsentRequiredError("capture is refused without a recorded consent basis")
    if mode not in {"copilot", "interview"}:
        raise ValueError("mode must be 'copilot' or 'interview'")
    t = _utc(now)
    remote_id: uuid.UUID | None = None
    if remote_name:
        # Human-declared at capture start. NOT voice-verified: recorded as operator_declared.
        from app.services.knowledge import upsert_stakeholder

        sh = upsert_stakeholder(
            session,
            client_id,
            remote_name,
            remote_role,
            remote_authority,
            True,
            f"declared by operator {recorded_by} at capture start; not voice-verified",
        )
        remote_id = sh.id
    m = Meeting(
        client_id=client_id,
        project_id=project_id,
        title=title,
        source="local_capture",
        status="live",
        consent_status="recorded",
        last_heartbeat_at=t,
        mode=mode,
        autopilot=autopilot,
        remote_stakeholder_id=remote_id,
    )
    session.add(m)
    session.flush()
    session.add(
        ConsentRecord(
            client_id=client_id, meeting_id=m.id, basis=consent_basis, recorded_by=recorded_by
        )
    )
    append_audit(
        session,
        "meeting.live_started",
        {
            "meeting_id": str(m.id),
            "consent_recorded_by": recorded_by,
            "mode": mode,
            "autopilot": autopilot,
            "declared_remote_speaker": remote_name,
        },
        client_id,
    )
    return m


def heartbeat(
    session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID, now: datetime | None = None
) -> Meeting:
    m = _live_meeting(session, client_id, meeting_id)
    if m.status not in LIVE_STATES:
        raise LiveError(f"meeting is {m.status!r}")
    m.last_heartbeat_at = _utc(now)
    _recover_if_degraded(session, m)
    return m


def _recover_if_degraded(session: Session, m: Meeting) -> None:
    if m.status == "degraded":
        m.status, m.degraded_reason = "live", None
        append_audit(session, "meeting.recovered", {"meeting_id": str(m.id)}, m.client_id)


def add_segments(
    session: Session,
    client_id: uuid.UUID,
    meeting_id: uuid.UUID,
    segments: list[LiveSegmentIn],
    now: datetime | None = None,
) -> int:
    m = _live_meeting(session, client_id, meeting_id)
    if m.status not in LIVE_STATES:
        raise LiveError(f"meeting is {m.status!r}; segments are not accepted")
    if m.consent_status != "recorded":
        raise ConsentRequiredError("no recorded consent")
    t = _utc(now)
    declared = None
    if m.remote_stakeholder_id:
        from app.persistence.models import Stakeholder

        sh = session.get(Stakeholder, m.remote_stakeholder_id)
        declared = sh.name if sh else None
    before = (
        session.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == m.id, TranscriptSegment.client_id == client_id)
        )
        or 0
    )
    n = before
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        session.add(
            TranscriptSegment(
                client_id=client_id,
                meeting_id=m.id,
                idx=n,
                speaker=s.speaker or (declared if s.channel == "loopback" else None),
                start_s=s.start_s,
                end_s=s.end_s,
                text=text,
                channel=s.channel,
            )
        )
        conversation.record_message(
            session,
            client_id,
            m.id,
            n,
            s.channel,
            s.speaker or (declared if s.channel == "loopback" else None),
            text,
            s.start_s,
            now=t,
        )
        n += 1
    if n > before:
        m.last_segment_at = t
        m.last_heartbeat_at = t
        _recover_if_degraded(session, m)
        if m.mode == "interview":
            cc = (
                session.scalar(
                    select(func.count())
                    .select_from(TranscriptSegment)
                    .where(
                        TranscriptSegment.meeting_id == m.id,
                        TranscriptSegment.client_id == client_id,
                        TranscriptSegment.channel != "agent",
                    )
                )
                or 0
            )
            jobs.enqueue(
                session,
                client_id,
                PLAN_JOB,
                f"{m.id}:{cc}",
                {"meeting_id": str(m.id), "count": cc},
            )
        elif n // WINDOW_EVERY_N_SEGMENTS > before // WINDOW_EVERY_N_SEGMENTS:
            jobs.enqueue(
                session,
                client_id,
                WINDOW_JOB,
                f"{m.id}:{n // WINDOW_EVERY_N_SEGMENTS}",
                {"meeting_id": str(m.id)},
            )
    session.flush()
    return n - before


def mark_stale_degraded(
    session: Session, now: datetime | None = None, max_age_s: float = HEARTBEAT_STALE_S
) -> int:
    """Called by the worker loop/API: a silent stream becomes a visible degraded meeting."""
    t = _utc(now)
    cutoff = t - timedelta(seconds=max_age_s)
    stale = session.scalars(
        select(Meeting).where(Meeting.status == "live", Meeting.last_heartbeat_at < cutoff)
    ).all()
    for m in stale:
        m.status = "degraded"
        m.degraded_reason = f"capture stream silent for more than {int(max_age_s)}s"
        append_audit(
            session,
            "meeting.degraded",
            {"meeting_id": str(m.id), "reason": m.degraded_reason},
            m.client_id,
        )
    return len(stale)


def end_meeting(
    session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID, now: datetime | None = None
) -> Meeting:
    m = _live_meeting(session, client_id, meeting_id)
    if m.status not in LIVE_STATES:
        raise LiveError(f"meeting is {m.status!r}; cannot end")
    m.ended_at = _utc(now)
    count = (
        session.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == m.id, TranscriptSegment.client_id == client_id)
        )
        or 0
    )
    if count == 0:
        m.status, m.failure_reason = "failed", "no transcript was captured"
        append_audit(session, "meeting.live_empty", {"meeting_id": str(m.id)}, client_id)
        return m
    m.status = "queued"  # final, persistent analysis runs through the normal durable job
    append_audit(
        session,
        "meeting.live_ended",
        {"meeting_id": str(m.id), "segments": count, "was_degraded": bool(m.degraded_reason)},
        client_id,
    )
    jobs.enqueue(session, client_id, ANALYZE_JOB, str(m.id), {"meeting_id": str(m.id)})
    return m


def suggest_now(
    session: Session,
    client_id: uuid.UUID,
    meeting_id: uuid.UUID,
    now: datetime | None = None,
    pause_s: float = PAUSE_S,
    cooldown_s: float = QUESTION_COOLDOWN_S,
) -> Suggestion:
    """At most one question, only during a pause, never while degraded. Code decides."""
    t = _utc(now)
    m = _live_meeting(session, client_id, meeting_id)
    mark_stale_degraded(session, t)
    if m.status == "degraded":
        return Suggestion(None, f"degraded: {m.degraded_reason}")
    if m.status != "live":
        return Suggestion(None, f"meeting is {m.status}")
    if m.last_segment_at is not None and (t - m.last_segment_at).total_seconds() < pause_s:
        return Suggestion(None, "speech in progress (waiting for a pause)")
    if m.last_question_at is not None and (t - m.last_question_at).total_seconds() < cooldown_s:
        return Suggestion(None, "cooldown after previous question")
    q = elicitation.next_question(session, client_id, meeting_id)
    if q is None:
        return Suggestion(None, "no open questions")
    m.last_question_at = t
    return Suggestion(q, "ready")


def mark_question(
    session: Session, client_id: uuid.UUID, question_id: uuid.UUID, action: str
) -> OpenQuestion:
    q = session.get(OpenQuestion, question_id)
    if q is None or q.client_id != client_id:
        raise LookupError("question not found in client scope")
    if action == "answered":
        return elicitation.record_answer(session, client_id, question_id)
    if action == "skipped":
        q.status = "dropped"
        append_audit(session, "question.skipped", {"question_id": str(q.id)}, client_id)
        return q
    raise ValueError("action must be 'answered' or 'skipped'")


def make_window_handler(
    provider: LLMProvider, settings: Settings, embedder: EmbeddingProvider | None
) -> Handler:
    """Incremental question mining over the latest window. Requirements are NOT persisted live
    (the final analysis owns that), so retries and re-runs cannot create duplicates."""

    async def handler(job: Job) -> None:
        session = object_session(job)
        assert session is not None
        m = session.get(Meeting, uuid.UUID(job.payload["meeting_id"]))
        if m is None or m.client_id != job.client_id:
            raise LookupError("meeting not found in client scope")
        if m.status != "live" or m.consent_status != "recorded":
            return
        rows = list(
            session.scalars(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.meeting_id == m.id, TranscriptSegment.client_id == m.client_id
                )
                .order_by(TranscriptSegment.idx.desc())
                .limit(WINDOW_SIZE)
            )
        )[::-1]
        if not rows:
            return
        segs = [
            SegmentIn(
                index=r.idx,
                speaker=r.speaker or (f"UNKNOWN ({r.channel})" if r.channel else None),
                text=r.text,
                start_s=r.start_s,
            )
            for r in rows
        ]
        result = await analyze(provider, str(m.client_id), segs, job.correlation_id)
        for q in result.questions:
            elicitation.add_question_if_new(
                session, embedder, m.client_id, m.id, q.question, q.why_it_matters, q.blocking
            )
        for f in result.injection_findings:
            append_audit(
                session,
                "analysis.injection_flagged",
                {"meeting_id": str(m.id), "rule": f.rule, "live": True},
                m.client_id,
            )

    return handler
