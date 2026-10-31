"""Interview mode: the agent speaks. Code decides WHEN; the LLM only proposes WHAT.

Flow: client segments arrive -> a plan job (LLM) stores the next utterance as `pending`, tagged
with the client-segment count it was based on -> the companion polls next_utterance(), which only
releases a FRESH pending utterance during a pause and after a cooldown -> the companion speaks it
and reports back via record_agent_said(), which stores it in the transcript as channel 'agent'
(context only; never client evidence) and updates checklist coverage.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.agents import interviewer
from app.agents.interviewer import OPENING, TOPICS, Line, welcome_back
from app.config import Settings
from app.orchestration import jobs
from app.orchestration.audit import append_audit
from app.orchestration.jobs import Handler
from app.persistence.models import Job, Meeting, TranscriptSegment
from app.providers.llm.base import LLMProvider
from app.services import conversation
from app.services.context import build_prior
from app.services.live import PLAN_JOB, mark_stale_degraded

PAUSE_S = (
    1.3  # client must be quiet this long (after speech was transcribed) before the agent speaks
)
WARM_JOB = "interview.warm"
COOLDOWN_S = 1.0  # after the agent spoke
NUDGE_AFTER_S = 25.0  # client silent after our question: let the model gently re-engage
CLOSE_GRACE_S = 12.0  # after the closing remarks, end the meeting
MAX_TURNS = 40
MAX_MINUTES = 45
WINDOW_LINES = 60


def _utc(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _meeting(session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID) -> Meeting:
    m = session.get(Meeting, meeting_id)
    if m is None or m.client_id != client_id:
        raise LookupError("meeting not found in client scope")
    return m


def client_segment_count(session: Session, m: Meeting) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(
                TranscriptSegment.meeting_id == m.id,
                TranscriptSegment.client_id == m.client_id,
                TranscriptSegment.channel != "agent",
            )
        )
        or 0
    )


def _state(m: Meeting) -> dict[str, Any]:
    return dict(m.interview_state or {})


def _save(m: Meeting, st: dict[str, Any]) -> None:
    m.interview_state = st  # reassign: JSONB change tracking needs a new object


def next_utterance(
    session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID, now: datetime | None = None
) -> dict[str, Any]:
    t = _utc(now)
    m = _meeting(session, client_id, meeting_id)
    mark_stale_degraded(session, t)
    if m.mode != "interview":
        return {"speak": None, "reason": "not an interview-mode meeting"}
    if m.status == "degraded":
        return {"speak": None, "reason": f"degraded: {m.degraded_reason}"}
    if m.status != "live":
        return {"speak": None, "reason": f"meeting is {m.status}"}
    st = _state(m)
    last_agent = datetime.fromisoformat(st["last_agent_at"]) if st.get("last_agent_at") else None
    if st.get("closed"):
        if last_agent and (t - last_agent).total_seconds() >= CLOSE_GRACE_S:
            return {"speak": None, "end": True, "reason": "closing remarks delivered"}
        return {"speak": None, "reason": "closing"}
    if not st.get("opened"):
        prior = build_prior(session, client_id, m.project_id, exclude_meeting_id=m.id)
        text = welcome_back(prior) if prior is not None else OPENING
        # while the greeting is spoken, start the LLM session so the first real reply is fast
        jobs.enqueue(
            session, client_id, WARM_JOB, f"{m.id}:warm", {"meeting_id": str(m.id)}, max_attempts=1
        )
        return {
            "speak": text,
            "action": "open",
            "topic": "intro",
            "reason": "opening (returning client)" if prior is not None else "opening",
        }
    if last_agent and (t - last_agent).total_seconds() < COOLDOWN_S:
        return {"speak": None, "reason": "cooldown after agent speech"}
    if m.last_segment_at is not None and (t - m.last_segment_at).total_seconds() < PAUSE_S:
        return {"speak": None, "reason": "client is speaking (waiting for a pause)"}
    cc = client_segment_count(session, m)
    pending = st.get("pending")
    if pending and pending.get("based_on") == cc:
        if m.last_segment_at is not None:  # how long the client has waited for this reply so far
            st["reply_wait_s"] = round((t - m.last_segment_at).total_seconds(), 2)
            _save(m, st)
        return {
            "speak": pending["text"],
            "action": pending["action"],
            "topic": pending.get("topic", "other"),
            "reason": "ready",
        }
    # nothing fresh: make sure a plan exists (nudge when the client has gone quiet on us)
    quiet_s = (t - last_agent).total_seconds() if last_agent else 0.0
    if not pending and cc == st.get("nudged_at_count") and quiet_s < NUDGE_AFTER_S:
        return {"speak": None, "reason": "waiting for the client"}
    if not pending and quiet_s >= NUDGE_AFTER_S and st.get("nudged_at_count") != cc:
        st["nudged_at_count"] = cc
        _save(m, st)
        jobs.enqueue(
            session,
            client_id,
            PLAN_JOB,
            f"{m.id}:{cc}:nudge{st.get('turns', 0)}",
            {"meeting_id": str(m.id), "count": cc, "nudge": True},
        )
    return {"speak": None, "reason": "planning the next question"}


def record_agent_said(
    session: Session,
    client_id: uuid.UUID,
    meeting_id: uuid.UUID,
    text: str,
    action: str,
    topic: str,
    now: datetime | None = None,
) -> Meeting:
    """The companion reports what it actually said aloud. Stored as channel 'agent'."""
    t = _utc(now)
    m = _meeting(session, client_id, meeting_id)
    n = (
        session.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == m.id, TranscriptSegment.client_id == client_id)
        )
        or 0
    )
    session.add(
        TranscriptSegment(
            client_id=client_id,
            meeting_id=m.id,
            idx=n,
            speaker="BA assistant",
            text=text.strip(),
            channel="agent",
        )
    )
    conversation.record_message(
        session, client_id, m.id, n, "agent", "BA assistant", text, None, now=t
    )
    st = _state(m)
    pending = st.pop("pending", None) or {}
    st["opened"] = True
    st["turns"] = int(st.get("turns", 0)) + (0 if action == "open" else 1)
    st["last_agent_at"] = t.isoformat()
    st["last_reply_wait_s"] = st.pop("reply_wait_s", None)
    covered = set(st.get("covered", [])) | set(pending.get("covered", []))
    st["covered"] = sorted(c for c in covered if c in TOPICS)
    if action == "confirm_back":
        st["last_confirm_idx"] = n
    if action == "close":
        st["closed"] = True
    _save(m, st)
    append_audit(
        session,
        "interview.agent_spoke",
        {
            "meeting_id": str(m.id),
            "action": action,
            "topic": topic,
            "turn": st["turns"],
            "reply_wait_s": st.get("last_reply_wait_s"),
        },
        client_id,
    )
    return m


def state_view(session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID) -> dict[str, Any]:
    m = _meeting(session, client_id, meeting_id)
    st = _state(m)
    cc = client_segment_count(session, m)
    covered = st.get("covered", [])
    return {
        "status": m.status,
        "mode": m.mode,
        "degraded_reason": m.degraded_reason,
        "turns": st.get("turns", 0),
        "client_segments": cc,
        "covered": covered,
        "remaining": [t for t in TOPICS if t not in covered],
        "coverage": round(len(covered) / len(TOPICS), 2),
        "closed": bool(st.get("closed")),
        "pending_preview": (st.get("pending") or {}).get("text"),
        "reply_wait_s": st.get("last_reply_wait_s"),
    }


def make_warm_handler(provider: LLMProvider) -> Handler:
    async def handler(job: Job) -> None:
        session = object_session(job)
        assert session is not None
        m = session.get(Meeting, uuid.UUID(job.payload["meeting_id"]))
        if m is None or m.client_id != job.client_id or m.status != "live":
            return
        await interviewer.warm_up(
            provider, str(m.client_id), f"{m.client_id}:{m.id}", correlation_id=job.correlation_id
        )

    return handler


def make_plan_handler(provider: LLMProvider, settings: Settings) -> Handler:
    async def handler(job: Job) -> None:
        session = object_session(job)
        assert session is not None
        m = session.get(Meeting, uuid.UUID(job.payload["meeting_id"]))
        if m is None or m.client_id != job.client_id:
            raise LookupError("meeting not found in client scope")
        if m.mode != "interview" or m.status != "live":
            return
        st = _state(m)
        if st.get("closed") or not st.get("opened"):
            return
        cc = client_segment_count(session, m)
        if cc > int(job.payload.get("count", cc)) and not job.payload.get("nudge"):
            return  # newer client speech exists; a newer plan job will handle it
        rows = list(
            session.scalars(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.meeting_id == m.id, TranscriptSegment.client_id == m.client_id
                )
                .order_by(TranscriptSegment.idx.desc())
                .limit(WINDOW_LINES)
            )
        )[::-1]
        lines = [Line(r.idx, r.channel == "agent", r.text) for r in rows]
        last_conf = st.get("last_confirm_idx", -1)
        since_confirm = sum(1 for r in rows if r.channel != "agent" and r.idx > last_conf)
        started = m.created_at
        over_time = (datetime.now(UTC) - started) > timedelta(minutes=MAX_MINUTES)
        prior = build_prior(session, m.client_id, m.project_id, exclude_meeting_id=m.id)
        turn = await interviewer.next_turn(
            provider,
            str(m.client_id),
            lines,
            st.get("covered", []),
            int(st.get("turns", 0)),
            since_confirm,
            force_close=int(st.get("turns", 0)) >= MAX_TURNS or over_time,
            correlation_id=job.correlation_id,
            prior=prior,
            warm_key=f"{m.client_id}:{m.id}",
        )
        st = _state(m)  # re-read: the agent may have spoken while we were thinking
        st["pending"] = {
            "text": turn.utterance,
            "action": turn.action,
            "topic": turn.topic,
            "covered": turn.covered_topics,
            "based_on": cc,
            "why": turn.reasoning_summary,
            "planned_at": datetime.now(UTC).isoformat(),
        }
        _save(m, st)
        append_audit(
            session,
            "interview.planned",
            {"meeting_id": str(m.id), "action": turn.action, "topic": turn.topic, "based_on": cc},
            m.client_id,
        )

    return handler
