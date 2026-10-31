"""meeting.analyze job handler: consent guard -> analyst -> grounded persistence -> purge."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, object_session

from app.agents.requirements_analyst import SegmentIn, analyze
from app.config import Settings
from app.orchestration import jobs
from app.orchestration.audit import append_audit
from app.orchestration.jobs import Handler
from app.persistence.models import (
    Evidence,
    Job,
    Meeting,
    Requirement,
    RequirementVersion,
    Stakeholder,
    TranscriptSegment,
)
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.services.autopilot import AUTOPILOT_JOB
from app.services.conflicts import detect_for_requirement
from app.services.elicitation import add_question_if_new
from app.services.retrieval import index_requirement


def _stakeholder_by_name(
    session: Session, client_id: uuid.UUID, name: str | None
) -> Stakeholder | None:
    if not name:
        return None
    return session.scalars(
        select(Stakeholder).where(
            Stakeholder.client_id == client_id, Stakeholder.name.ilike(name.strip())
        )
    ).first()


def make_analyze_handler(
    provider: LLMProvider, settings: Settings, embedder: EmbeddingProvider | None = None
) -> Handler:
    async def handler(job: Job) -> None:
        session = object_session(job)
        assert session is not None
        meeting = session.get(Meeting, uuid.UUID(job.payload["meeting_id"]))
        if meeting is None or meeting.client_id != job.client_id:
            raise LookupError("meeting not found in client scope")
        # Consent & Privacy Guard (deterministic): hard stop without consent.
        if meeting.consent_status != "recorded":
            meeting.status = "failed"
            meeting.failure_reason = "no recorded consent"
            raise PermissionError("meeting has no recorded consent; refusing to process")
        meeting.status = "analyzing"
        # Commit now: the LLM call below takes minutes, and an uncommitted write would hold the
        # meeting row lock the whole time, stalling live interview updates on the same row.
        session.commit()
        rows = list(
            session.scalars(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.meeting_id == meeting.id,
                    TranscriptSegment.client_id == meeting.client_id,
                )
                .order_by(TranscriptSegment.idx)
            )
        )
        if not rows:
            meeting.status = "failed"
            meeting.failure_reason = "no transcript segments (already purged or empty)"
            raise LookupError(meeting.failure_reason)
        segs = [
            SegmentIn(
                index=r.idx, speaker=r.speaker, text=r.text, start_s=r.start_s, channel=r.channel
            )
            for r in rows
        ]
        try:
            result = await analyze(provider, str(meeting.client_id), segs, job.correlation_id)
        except Exception as exc:
            meeting.status = "failed"
            meeting.failure_reason = f"{type(exc).__name__}: {exc}"[:500]
            raise
        expires = datetime.now(UTC) + timedelta(days=settings.evidence_retention_days)
        new_ids: set[uuid.UUID] = set()
        new_reqs: list[Requirement] = []
        try:
            # Savepoint: if anything below fails, ALL derived writes roll back so a retry
            # cannot create duplicates. The failure itself is recorded after the rollback.
            with session.begin_nested():
                for g in result.accepted:
                    sh = _stakeholder_by_name(session, meeting.client_id, g.speaker)
                    req = Requirement(
                        client_id=meeting.client_id,
                        project_id=meeting.project_id,
                        meeting_id=meeting.id,
                        title=g.requirement.title,
                        statement=g.requirement.statement,
                        kind=g.requirement.kind,
                        explicit=g.requirement.explicit,
                        status="extracted",
                    )
                    session.add(req)
                    session.flush()
                    new_reqs.append(req)
                    new_ids.add(req.id)
                    session.add(
                        RequirementVersion(
                            client_id=meeting.client_id,
                            requirement_id=req.id,
                            version=1,
                            title=req.title,
                            statement=req.statement,
                            change_reason="initial extraction",
                        )
                    )
                    session.add(
                        Evidence(
                            client_id=meeting.client_id,
                            meeting_id=meeting.id,
                            requirement_id=req.id,
                            segment_index=g.requirement.segment_index,
                            quote=g.requirement.quote,
                            speaker_label=g.speaker,
                            stakeholder_id=sh.id if sh else None,
                            attribution_confidence=None,
                            attribution_source=(
                                "operator_declared"
                                if meeting.remote_stakeholder_id and g.channel == "loopback"
                                else "source_label"
                            ),
                            confirmation_quote=g.confirmation_quote,
                            start_s=g.start_s,
                            expires_at=expires,
                        )
                    )
                by_title = {r.title.strip().casefold(): str(r.id) for r in new_reqs}
                for q in result.questions:
                    blocks = [
                        by_title[t.strip().casefold()]
                        for t in q.blocks_requirement_titles
                        if t.strip().casefold() in by_title
                    ]
                    add_question_if_new(
                        session,
                        embedder,
                        meeting.client_id,
                        meeting.id,
                        q.question,
                        q.why_it_matters,
                        q.blocking and bool(blocks),  # blocking only if it blocks something real
                        blocks,
                    )
                # Rejections and injection findings are surfaced in the audit trail, never dropped.
                for rej in result.rejected:
                    append_audit(
                        session,
                        "analysis.requirement_rejected",
                        {
                            "meeting_id": str(meeting.id),
                            "title": rej.requirement.title,
                            "reason": rej.reason,
                        },
                        meeting.client_id,
                    )
                for f in result.injection_findings:
                    append_audit(
                        session,
                        "analysis.injection_flagged",
                        {
                            "meeting_id": str(meeting.id),
                            "rule": f.rule,
                            "segment_index": f.segment_index,
                        },
                        meeting.client_id,
                    )
                if embedder is not None:
                    for r in new_reqs:
                        index_requirement(session, embedder, r, expires)
                    for r in new_reqs:
                        # Sibling pairs are recorded once: skip siblings with a larger id.
                        later = {n for n in new_ids if n > r.id}
                        await detect_for_requirement(
                            session, provider, embedder, r, later, job.correlation_id
                        )
                # Last, after every await: dirtying the meeting earlier would hold its row lock
                # across LLM calls (conflict detection) and stall the live interview.
                meeting.status = "analyzed"
                append_audit(
                    session,
                    "meeting.analyzed",
                    {
                        "meeting_id": str(meeting.id),
                        "accepted": len(result.accepted),
                        "rejected": len(result.rejected),
                        "questions": len(result.questions),
                    },
                    meeting.client_id,
                )
        except Exception as exc:
            meeting.status = "failed"
            meeting.failure_reason = f"{type(exc).__name__}: {exc}"[:500]
            raise
        if meeting.autopilot:  # independent follow-through: enqueued durably, after success only
            jobs.enqueue(
                session,
                meeting.client_id,
                AUTOPILOT_JOB,
                str(meeting.id),
                {"meeting_id": str(meeting.id)},
            )
        if settings.transcript_grace_hours <= 0:
            purge_transcript(session, meeting)

    return handler


def purge_transcript(session: Session, meeting: Meeting) -> int:
    n = session.execute(
        delete(TranscriptSegment).where(
            TranscriptSegment.meeting_id == meeting.id,
            TranscriptSegment.client_id == meeting.client_id,
        )
    ).rowcount  # type: ignore[attr-defined]
    meeting.transcript_purged_at = datetime.now(UTC)
    append_audit(
        session,
        "transcript.purged",
        {"meeting_id": str(meeting.id), "segments": n},
        meeting.client_id,
    )
    return int(n)


HandlerFactory = Callable[[LLMProvider, Settings], Handler]
