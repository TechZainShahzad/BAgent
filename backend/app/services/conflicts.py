import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents.conflict_detector import CANDIDATE_K, CANDIDATE_MIN_SIMILARITY, judge_pair
from app.orchestration.audit import append_audit
from app.persistence.models import ConflictRecord, Requirement
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.services.retrieval import chunk_text_for, search

APPROVED = {"confirmed", "ready_for_delivery", "synced"}


async def detect_for_requirement(
    session: Session,
    provider: LLMProvider,
    embedder: EmbeddingProvider,
    req: Requirement,
    skip_ids: set[uuid.UUID] | None = None,
    correlation_id: str = "-",
) -> list[ConflictRecord]:
    """Find duplicates/contradictions among the SAME client's (and project's) current knowledge."""
    found: list[ConflictRecord] = []
    result = search(
        session,
        embedder,
        req.client_id,
        chunk_text_for(req),
        project_id=req.project_id,
        k=CANDIDATE_K,
        min_similarity=CANDIDATE_MIN_SIMILARITY,
        exclude_source_id=req.id,
    )
    for hit in result.hits:
        if hit.source_type != "requirement" or (skip_ids and hit.source_id in skip_ids):
            continue
        other = session.get(Requirement, hit.source_id)
        if other is None or other.client_id != req.client_id:
            continue  # scope assertion: never compare across clients
        verdict = await judge_pair(
            provider,
            str(req.client_id),
            chunk_text_for(req),
            chunk_text_for(other),
            str(other.id),
            hit.similarity,
            correlation_id,
        )
        if verdict.relation == "unrelated":
            continue
        rec = ConflictRecord(
            client_id=req.client_id,
            requirement_id=req.id,
            other_requirement_id=other.id,
            relation=verdict.relation,
            similarity=hit.similarity,
            explanation=verdict.explanation,
        )
        session.add(rec)
        append_audit(
            session,
            "conflict.recorded",
            {
                "requirement_id": str(req.id),
                "other_id": str(other.id),
                "relation": verdict.relation,
                "method": verdict.method,
                "similarity": round(hit.similarity, 3),
                "other_status": other.status,
            },
            req.client_id,
        )
        found.append(rec)
    session.flush()
    return found


def has_open_conflict_with_approved(session: Session, req: Requirement) -> bool:
    rows = session.scalars(
        select(ConflictRecord).where(
            ConflictRecord.client_id == req.client_id,
            ConflictRecord.status == "open",
            ConflictRecord.relation == "contradiction",
            or_(
                ConflictRecord.requirement_id == req.id,
                ConflictRecord.other_requirement_id == req.id,
            ),
        )
    )
    for rec in rows:
        counterpart = (
            rec.other_requirement_id if rec.requirement_id == req.id else rec.requirement_id
        )
        other = session.get(Requirement, counterpart)
        if other is not None and other.client_id == req.client_id and other.status in APPROVED:
            return True
    return False
