"""Elicitation: question hygiene and one-at-a-time selection. Deterministic ranking.

- New questions that duplicate an open/asked/answered question of the same client are suppressed
  (and audited), so answered questions are never asked again.
- next_question() returns exactly one question: blocking first, then most-referenced topic.
"""

import math
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orchestration.audit import append_audit
from app.persistence.models import OpenQuestion
from app.providers.embeddings.base import EmbeddingProvider

DUPLICATE_SIMILARITY = 0.85


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def find_duplicate(
    session: Session, embedder: EmbeddingProvider, client_id: uuid.UUID, text: str
) -> OpenQuestion | None:
    existing = list(
        session.scalars(
            select(OpenQuestion).where(
                OpenQuestion.client_id == client_id, OpenQuestion.status != "dropped"
            )
        )
    )
    if not existing:
        return None
    vecs = embedder.embed([text] + [q.question for q in existing])
    best, best_sim = None, 0.0
    for q, v in zip(existing, vecs[1:], strict=True):
        sim = _cos(vecs[0], v)
        if sim > best_sim:
            best, best_sim = q, sim
    return best if best_sim >= DUPLICATE_SIMILARITY else None


def add_question_if_new(
    session: Session,
    embedder: EmbeddingProvider | None,
    client_id: uuid.UUID,
    meeting_id: uuid.UUID,
    question: str,
    why: str,
    blocking: bool,
    blocks_requirement_ids: list[str] | None = None,
) -> OpenQuestion | None:
    if embedder is not None:
        dup = find_duplicate(session, embedder, client_id, question)
        if dup is not None:
            append_audit(
                session,
                "question.suppressed_duplicate",
                {"existing_question_id": str(dup.id), "existing_status": dup.status},
                client_id,
            )
            if blocking and not dup.blocking and dup.status == "open":
                dup.blocking = True  # escalate priority, don't re-ask
            return None
    q = OpenQuestion(
        client_id=client_id,
        meeting_id=meeting_id,
        question=question,
        why_it_matters=why,
        blocking=blocking,
        blocks_requirement_ids=blocks_requirement_ids or [],
    )
    session.add(q)
    session.flush()
    return q


def next_question(
    session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID | None = None
) -> OpenQuestion | None:
    """The single highest-priority open question, marked as asked. Never returns asked/answered."""
    stmt = select(OpenQuestion).where(
        OpenQuestion.client_id == client_id, OpenQuestion.status == "open"
    )
    if meeting_id is not None:
        stmt = stmt.where(OpenQuestion.meeting_id == meeting_id)
    q = session.scalars(
        stmt.order_by(OpenQuestion.blocking.desc(), OpenQuestion.created_at).limit(1)
    ).first()
    if q is None:
        return None
    q.status = "asked"
    append_audit(session, "question.asked", {"question_id": str(q.id)}, client_id)
    return q


def record_answer(session: Session, client_id: uuid.UUID, question_id: uuid.UUID) -> OpenQuestion:
    q = session.get(OpenQuestion, question_id)
    if q is None or q.client_id != client_id:
        raise LookupError("question not found in client scope")
    q.status = "answered"
    append_audit(session, "question.answered", {"question_id": str(q.id)}, client_id)
    return q
