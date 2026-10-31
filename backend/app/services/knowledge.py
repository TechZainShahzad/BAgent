import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.domain.requirement import ReqStatus, TransitionContext, check_transition
from app.orchestration.audit import append_audit
from app.persistence.models import (
    ConversationMessage,
    Evidence,
    KnowledgeChunk,
    Meeting,
    OpenQuestion,
    PurgeRun,
    Requirement,
    RequirementVersion,
    Stakeholder,
    StoryDraft,
    TranscriptSegment,
)
from app.providers.embeddings.base import EmbeddingProvider
from app.services.conflicts import has_open_conflict_with_approved
from app.services.retrieval import mark_not_current, reindex_requirement


def upsert_stakeholder(
    session: Session,
    client_id: uuid.UUID,
    name: str,
    role: str | None = None,
    has_decision_authority: bool = False,
    identity_verified: bool = False,
    verification_note: str | None = None,
) -> Stakeholder:
    sh = session.scalars(
        select(Stakeholder).where(Stakeholder.client_id == client_id, Stakeholder.name == name)
    ).first()
    if sh is None:
        sh = Stakeholder(client_id=client_id, name=name)
        session.add(sh)
    sh.role, sh.has_decision_authority = role, has_decision_authority
    sh.identity_verified, sh.verification_note = identity_verified, verification_note
    session.flush()
    # A human naming/verifying a stakeholder links evidence carrying that speaker label.
    session.execute(
        update(Evidence)
        .where(
            Evidence.client_id == client_id,
            Evidence.stakeholder_id.is_(None),
            Evidence.speaker_label.ilike(name.strip()),
        )
        .values(stakeholder_id=sh.id)
    )
    append_audit(
        session,
        "stakeholder.upserted",
        {
            "stakeholder_id": str(sh.id),
            "authority": has_decision_authority,
            "verified": identity_verified,
        },
        client_id,
    )
    return sh


def _ac_measurable(session: Session, req: Requirement) -> bool:
    """True only if the latest draft for the CURRENT version passed the Testability Reviewer."""
    draft = session.scalars(
        select(StoryDraft)
        .where(
            StoryDraft.requirement_id == req.id,
            StoryDraft.client_id == req.client_id,
            StoryDraft.requirement_version == req.current_version,
        )
        .order_by(StoryDraft.created_at.desc())
        .limit(1)
    ).first()
    return bool(draft and draft.ac_measurable)


def _context(session: Session, req: Requirement) -> TransitionContext:
    evidence = list(
        session.scalars(
            select(Evidence).where(
                Evidence.requirement_id == req.id, Evidence.client_id == req.client_id
            )
        )
    )
    authority = False
    for ev in evidence:
        if ev.stakeholder_id:
            sh = session.get(Stakeholder, ev.stakeholder_id)
            if (
                sh
                and sh.client_id == req.client_id
                and sh.identity_verified
                and sh.has_decision_authority
            ):
                authority = True
    blocking = (
        session.scalar(
            select(func.count())
            .select_from(OpenQuestion)
            .where(
                OpenQuestion.client_id == req.client_id,
                OpenQuestion.blocking.is_(True),
                OpenQuestion.blocks_requirement_ids.contains([str(req.id)]),
                OpenQuestion.status == "open",
            )
        )
        or 0
    )
    return TransitionContext(
        has_evidence=any(e.grounded for e in evidence),
        authority_verified=authority,
        acceptance_criteria_measurable=_ac_measurable(session, req),
        open_blocking_questions=blocking,
        has_conflict_with_approved_rule=has_open_conflict_with_approved(session, req),
    )


def transition_requirement(
    session: Session,
    client_id: uuid.UUID,
    requirement_id: uuid.UUID,
    target: ReqStatus,
    reason: str,
) -> Requirement:
    req = session.get(Requirement, requirement_id)
    if req is None or req.client_id != client_id:
        raise LookupError("requirement not found in client scope")
    current = ReqStatus(req.status)
    check_transition(current, target, _context(session, req))  # raises TransitionError
    req.status = target.value
    if target in {ReqStatus.SUPERSEDED, ReqStatus.REJECTED, ReqStatus.CANCELLED}:
        mark_not_current(session, client_id, req.id)  # retrieval prefers current knowledge
    append_audit(
        session,
        "requirement.transitioned",
        {
            "requirement_id": str(req.id),
            "from": current.value,
            "to": target.value,
            "reason": reason,
        },
        client_id,
    )
    return req


def revise_requirement(
    session: Session,
    client_id: uuid.UUID,
    requirement_id: uuid.UUID,
    title: str,
    statement: str,
    reason: str,
    embedder: EmbeddingProvider | None = None,
) -> RequirementVersion:
    req = session.get(Requirement, requirement_id)
    if req is None or req.client_id != client_id:
        raise LookupError("requirement not found in client scope")
    req.current_version += 1
    req.title, req.statement = title, statement
    v = RequirementVersion(
        client_id=client_id,
        requirement_id=req.id,
        version=req.current_version,
        title=title,
        statement=statement,
        change_reason=reason,
    )
    session.add(v)
    append_audit(
        session,
        "requirement.revised",
        {"requirement_id": str(req.id), "version": req.current_version},
        client_id,
    )
    if embedder is not None:
        session.flush()
        reindex_requirement(session, embedder, req)
    return v


def purge_expired(
    session: Session,
    now: datetime | None = None,
    grace_hours: float = 0.0,
    failed_ttl_hours: float = 72.0,
) -> PurgeRun:
    """Retention job: expired evidence + derived chunks, and transcript segments that outlived
    their allowance (analyzed meetings past the grace period, failed/abandoned ones past the
    failed TTL). Live meetings are never touched. Audited."""
    now = now or datetime.now(UTC)
    ev = session.execute(delete(Evidence).where(Evidence.expires_at <= now)).rowcount  # type: ignore[attr-defined]
    chunks = session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.expires_at <= now)
    ).rowcount  # type: ignore[attr-defined]
    conv = session.execute(
        delete(ConversationMessage).where(ConversationMessage.expires_at <= now)
    ).rowcount  # type: ignore[attr-defined]
    analyzed_cutoff = now - timedelta(hours=grace_hours)
    failed_cutoff = now - timedelta(hours=failed_ttl_hours)
    done = select(Meeting.id).where(
        (Meeting.status == "analyzed") & (Meeting.created_at <= analyzed_cutoff)
        | (Meeting.status.in_(("failed", "degraded")) & (Meeting.created_at <= failed_cutoff))
    )
    segs = session.execute(
        delete(TranscriptSegment).where(TranscriptSegment.meeting_id.in_(done))
    ).rowcount  # type: ignore[attr-defined]
    for m in session.scalars(
        select(Meeting).where(
            Meeting.id.in_(
                select(Meeting.id).where(
                    Meeting.status.in_(("analyzed", "failed", "degraded")),
                    Meeting.transcript_purged_at.is_(None),
                    Meeting.created_at <= analyzed_cutoff,
                )
            )
        )
    ):
        remaining = session.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(TranscriptSegment.meeting_id == m.id)
        )
        if not remaining:
            m.transcript_purged_at = now
    run = PurgeRun(ran_at=now, evidence_deleted=ev or 0, segments_deleted=segs or 0)
    session.add(run)
    session.flush()
    append_audit(
        session,
        "retention.purge_run",
        {
            "purge_run_id": str(run.id),
            "evidence_deleted": run.evidence_deleted,
            "chunks_deleted": chunks or 0,
            "segments_deleted": run.segments_deleted,
            "conversation_messages_deleted": conv or 0,
        },
    )
    return run


def count_segments(session: Session, client_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TranscriptSegment)
            .where(TranscriptSegment.client_id == client_id)
        )
        or 0
    )


def apply_correction(
    session: Session,
    client_id: uuid.UUID,
    requirement_id: uuid.UUID,
    title: str,
    statement: str,
    reason: str,
    corrected_by: str,
    embedder: EmbeddingProvider | None = None,
) -> Requirement:
    """Attendee/operator correction: new immutable version; approved states go back to
    clarification (needs re-confirmation). The old version is preserved, never edited."""
    req = session.get(Requirement, requirement_id)
    if req is None or req.client_id != client_id:
        raise LookupError("requirement not found in client scope")
    if not reason.strip():
        raise ValueError("a correction needs a reason")
    revise_requirement(
        session,
        client_id,
        requirement_id,
        title,
        statement,
        f"correction by {corrected_by}: {reason}",
        embedder,
    )
    if req.status in {"confirmed", "ready_for_delivery"}:
        transition_requirement(
            session,
            client_id,
            requirement_id,
            ReqStatus.UNDER_CLARIFICATION,
            "re-confirmation required after correction",
        )
    append_audit(
        session,
        "requirement.corrected",
        {"requirement_id": str(req.id), "by": corrected_by, "version": req.current_version},
        client_id,
    )
    return req
