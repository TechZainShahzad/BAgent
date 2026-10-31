"""Scoped knowledge retrieval. Every query is filtered by client_id (and project_id when given).
An empty result is explicit, never silently padded with out-of-scope data."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.errors import ScopeError
from app.persistence.models import KnowledgeChunk, Requirement
from app.providers.embeddings.base import EmbeddingProvider


@dataclass(frozen=True)
class Hit:
    chunk_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    meeting_id: uuid.UUID | None
    text: str
    similarity: float  # cosine similarity, 1.0 = identical
    is_current: bool


@dataclass(frozen=True)
class RetrievalResult:
    hits: list[Hit]
    client_id: uuid.UUID
    project_id: uuid.UUID | None

    @property
    def empty(self) -> bool:
        return not self.hits


def chunk_text_for(req: Requirement) -> str:
    return f"{req.title}. {req.statement}"


def index_requirement(
    session: Session, embedder: EmbeddingProvider, req: Requirement, expires_at: datetime
) -> KnowledgeChunk:
    (vec,) = embedder.embed([chunk_text_for(req)])
    chunk = KnowledgeChunk(
        client_id=req.client_id,
        project_id=req.project_id,
        source_type="requirement",
        source_id=req.id,
        meeting_id=req.meeting_id,
        text=chunk_text_for(req),
        embedding=vec,
        expires_at=expires_at,
    )
    session.add(chunk)
    session.flush()
    return chunk


def reindex_requirement(session: Session, embedder: EmbeddingProvider, req: Requirement) -> None:
    old = session.scalars(
        select(KnowledgeChunk).where(
            KnowledgeChunk.client_id == req.client_id,
            KnowledgeChunk.source_type == "requirement",
            KnowledgeChunk.source_id == req.id,
        )
    ).all()
    expires = old[0].expires_at if old else None
    session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.client_id == req.client_id, KnowledgeChunk.source_id == req.id
        )
    )
    if expires is not None:
        index_requirement(session, embedder, req, expires)


def search(
    session: Session,
    embedder: EmbeddingProvider,
    client_id: uuid.UUID,
    query: str,
    project_id: uuid.UUID | None = None,
    k: int = 5,
    min_similarity: float = 0.0,
    current_only: bool = True,
    exclude_source_id: uuid.UUID | None = None,
) -> RetrievalResult:
    if client_id is None:
        raise ScopeError("client_id is required for retrieval")
    (qvec,) = embedder.embed([query])
    dist = KnowledgeChunk.embedding.cosine_distance(qvec)
    stmt = select(KnowledgeChunk, dist.label("dist")).where(KnowledgeChunk.client_id == client_id)
    if project_id is not None:
        stmt = stmt.where(KnowledgeChunk.project_id == project_id)
    if current_only:
        stmt = stmt.where(KnowledgeChunk.is_current.is_(True))
    if exclude_source_id is not None:
        stmt = stmt.where(KnowledgeChunk.source_id != exclude_source_id)
    rows = session.execute(stmt.order_by(dist).limit(k)).all()
    hits = [
        Hit(c.id, c.source_type, c.source_id, c.meeting_id, c.text, 1.0 - float(d), c.is_current)
        for c, d in rows
        if 1.0 - float(d) >= min_similarity
    ]
    return RetrievalResult(hits, client_id, project_id)


def mark_not_current(session: Session, client_id: uuid.UUID, source_id: uuid.UUID) -> None:
    for c in session.scalars(
        select(KnowledgeChunk).where(
            KnowledgeChunk.client_id == client_id, KnowledgeChunk.source_id == source_id
        )
    ):
        c.is_current = False
