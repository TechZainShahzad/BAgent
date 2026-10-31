import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.config import Settings
from app.domain.requirement import ReqStatus, TransitionError
from app.integrations.transcript_file.parser import ParsedSegment
from app.orchestration import jobs
from app.orchestration.runner import JobRunner
from app.persistence.models import (
    Client,
    ConflictRecord,
    Evidence,
    KnowledgeChunk,
    Meeting,
    Requirement,
    Stakeholder,
)
from app.providers.llm.base import UsageLimitError
from app.providers.llm.fake import FakeLLMProvider
from app.services import knowledge, meetings
from app.services.analysis import make_analyze_handler
from app.services.retrieval import index_requirement, search

TOPICS = {"password": 0, "dashboard": 1, "refund": 2, "audit": 3}


class TopicEmbedder:
    """Deterministic test embedder: same topic keyword -> identical vector; else orthogonal."""

    name = "topic"
    dimensions = 384

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 384
            idx = next((i for k, i in TOPICS.items() if k in t.lower()), 100)
            v[idx] = 1.0
            out.append(v)
        return out


def _req(client_id, title, statement, status="extracted", meeting_id=None):
    return Requirement(
        client_id=client_id,
        title=title,
        statement=statement,
        kind="rule",
        explicit=True,
        status=status,
        meeting_id=meeting_id,
    )


def _new_client(s):
    c = Client(name=f"c-{uuid.uuid4().hex[:8]}")
    s.add(c)
    s.flush()
    return c.id


FUTURE = datetime.now(UTC) + timedelta(days=30)


def test_search_is_client_scoped_on_the_vector_path(factory):
    emb = TopicEmbedder()
    with factory() as s:
        a, b = _new_client(s), _new_client(s)
        ra = _req(a, "Password reset", "reset password by email")
        rb = _req(b, "Password reset B", "reset password by SMS")
        s.add_all([ra, rb])
        s.flush()
        index_requirement(s, emb, ra, FUTURE)
        index_requirement(s, emb, rb, FUTURE)
        s.commit()
        res = search(s, emb, a, "how do users reset a password", k=10)
        assert [h.source_id for h in res.hits] == [ra.id]
        assert all(h.source_id != rb.id for h in search(s, emb, a, "password", k=50).hits)


def test_project_scope_and_empty_retrieval(factory):
    emb = TopicEmbedder()
    with factory() as s:
        a = _new_client(s)
        r = _req(a, "Dashboard", "dashboard shows logins")
        s.add(r)
        s.flush()
        index_requirement(s, emb, r, FUTURE)
        s.commit()
        res = search(s, emb, a, "refund approvals", min_similarity=0.5)
        assert res.empty and res.client_id == a  # explicit empty, not padded
        assert search(s, emb, a, "dashboard", project_id=uuid.uuid4()).empty


def test_superseded_knowledge_is_not_current(factory):
    emb = TopicEmbedder()
    with factory() as s:
        a = _new_client(s)
        r = _req(a, "Refund rule", "refund limit 500", status="extracted")
        s.add(r)
        s.flush()
        index_requirement(s, emb, r, FUTURE)
        s.flush()
        knowledge.transition_requirement(s, a, r.id, ReqStatus.SUPERSEDED, "replaced")
        s.commit()
        assert search(s, emb, a, "refund", min_similarity=0.5).empty
        assert not search(s, emb, a, "refund", current_only=False, min_similarity=0.5).empty


SEGS = [
    ParsedSegment(
        index=0, speaker="Priya", text="Refunds above 1000 dollars need manager approval."
    )
]
OUT = {
    "requirements": [
        {
            "title": "Refund approval",
            "statement": "Refunds above 1000 dollars need manager approval",
            "kind": "rule",
            "explicit": True,
            "actors": [],
            "segment_index": 0,
            "quote": "Refunds above 1000 dollars need manager approval",
        }
    ],
    "open_questions": [],
}


def _llm(relation):
    def respond(req):
        if req.agent == "conflict_detector":
            return {"relation": relation, "explanation": "limit differs: 500 vs 1000"}
        return OUT

    return respond


async def _analyze(factory, client_id, provider, embedder):
    with factory() as s:
        meetings.import_segments(s, client_id, SEGS, "M", "attested", "tester")
        s.commit()
    runner = JobRunner(
        factory,
        {meetings.ANALYZE_JOB: make_analyze_handler(provider, Settings(), embedder)},
        client_id=client_id,
    )
    return await runner.run_until_empty()


async def test_contradiction_with_approved_blocks_confirmation(factory, client_id):
    emb = TopicEmbedder()
    with factory() as s:
        old = _req(
            client_id,
            "Refund limit",
            "refunds above 500 dollars need manager approval",
            status="confirmed",
        )
        s.add(old)
        s.flush()
        index_requirement(s, emb, old, FUTURE)
        s.commit()
        old_id = old.id
    assert await _analyze(factory, client_id, FakeLLMProvider(_llm("contradiction")), emb) == 1
    with factory() as s:
        rec = s.scalars(select(ConflictRecord).where(ConflictRecord.client_id == client_id)).one()
        assert rec.relation == "contradiction" and rec.status == "open"
        assert rec.other_requirement_id == old_id
        new = s.get(Requirement, rec.requirement_id)
        assert new.status == "extracted"  # never silently overwrote or resolved
        assert s.get(Requirement, old_id).statement.startswith("refunds above 500")
        # verified authority exists, yet the conflict still blocks confirmation
        sh = knowledge.upsert_stakeholder(
            s, client_id, "Priya", has_decision_authority=True, identity_verified=True
        )
        s.execute(
            update(Evidence).where(Evidence.requirement_id == new.id).values(stakeholder_id=sh.id)
        )
        with pytest.raises(TransitionError, match="conflict"):
            knowledge.transition_requirement(s, client_id, new.id, ReqStatus.CONFIRMED, "try")


async def test_unrelated_creates_no_record_and_duplicate_is_recorded(factory, client_id):
    emb = TopicEmbedder()
    with factory() as s:
        old = _req(client_id, "Refund limit", "refund policy", status="extracted")
        s.add(old)
        s.flush()
        index_requirement(s, emb, old, FUTURE)
        s.commit()
    await _analyze(factory, client_id, FakeLLMProvider(_llm("unrelated")), emb)
    with factory() as s:
        assert not list(
            s.scalars(select(ConflictRecord).where(ConflictRecord.client_id == client_id))
        )


async def test_duplicate_relation_recorded(factory, client_id):
    emb = TopicEmbedder()
    with factory() as s:
        old = _req(client_id, "Refund limit", "refund policy")
        s.add(old)
        s.flush()
        index_requirement(s, emb, old, FUTURE)
        s.commit()
    await _analyze(factory, client_id, FakeLLMProvider(_llm("duplicate")), emb)
    with factory() as s:
        rec = s.scalars(select(ConflictRecord).where(ConflictRecord.client_id == client_id)).one()
        assert rec.relation == "duplicate"


async def test_other_clients_knowledge_is_never_compared(factory, client_id):
    emb = TopicEmbedder()
    with factory() as s:
        other = _new_client(s)
        foreign = _req(other, "Refund limit", "refund 500", status="confirmed")
        s.add(foreign)
        s.flush()
        index_requirement(s, emb, foreign, FUTURE)
        s.commit()
    prov = FakeLLMProvider(_llm("contradiction"))
    await _analyze(factory, client_id, prov, emb)
    assert not [c for c in prov.calls if c.agent == "conflict_detector"]
    with factory() as s:
        assert not list(
            s.scalars(select(ConflictRecord).where(ConflictRecord.client_id == client_id))
        )


async def test_failure_in_conflict_check_rolls_back_so_retry_cannot_duplicate(
    factory, client_id, monkeypatch
):
    monkeypatch.setattr(jobs, "BACKOFF_BASE_S", 0.0)
    emb = TopicEmbedder()
    with factory() as s:
        old = _req(client_id, "Refund limit", "refund policy")
        s.add(old)
        s.flush()
        index_requirement(s, emb, old, FUTURE)
        s.commit()

    def respond(req):
        return UsageLimitError("limit") if req.agent == "conflict_detector" else OUT

    ran = await _analyze(factory, client_id, FakeLLMProvider(respond), emb)
    assert ran == 3
    with factory() as s:
        titles = [
            r.title
            for r in s.scalars(select(Requirement).where(Requirement.client_id == client_id))
        ]
        assert titles == ["Refund limit"]  # new requirement never persisted partially
        m = s.scalars(select(Meeting).where(Meeting.client_id == client_id)).one()
        assert m.status == "failed" and "UsageLimitError" in m.failure_reason
        assert (
            len(
                list(s.scalars(select(KnowledgeChunk).where(KnowledgeChunk.client_id == client_id)))
            )
            == 1
        )


async def test_retention_purge_removes_derived_chunks(factory, client_id):
    emb = TopicEmbedder()
    with factory() as s:
        r = _req(client_id, "Audit log", "audit everything")
        s.add(r)
        s.flush()
        index_requirement(s, emb, r, datetime.now(UTC) - timedelta(days=1))
        s.commit()
        knowledge.purge_expired(s)
        s.commit()
        assert not list(
            s.scalars(select(KnowledgeChunk).where(KnowledgeChunk.client_id == client_id))
        )


def test_revise_reindexes_embedding(factory):
    emb = TopicEmbedder()
    with factory() as s:
        a = _new_client(s)
        r = _req(a, "Password reset", "reset password by email")
        s.add(r)
        s.flush()
        index_requirement(s, emb, r, FUTURE)
        s.flush()
        knowledge.revise_requirement(
            s, a, r.id, "Dashboard", "dashboard logins", "scope change", emb
        )
        s.commit()
        assert search(s, emb, a, "password", min_similarity=0.5).empty
        assert [h.source_id for h in search(s, emb, a, "dashboard", min_similarity=0.5).hits] == [
            r.id
        ]


def test_stakeholder_isolated_by_client_in_confirmation(factory, client_id):
    with factory() as s:
        other = _new_client(s)
        foreign = knowledge.upsert_stakeholder(
            s, other, "Alice", has_decision_authority=True, identity_verified=True
        )
        r = _req(client_id, "X req", "audit something")
        s.add(r)
        s.flush()
        s.add(
            Evidence(
                client_id=client_id,
                meeting_id=_meeting(s, client_id),
                requirement_id=r.id,
                segment_index=0,
                quote="audit something",
                stakeholder_id=foreign.id,
                expires_at=FUTURE,
            )
        )
        s.flush()
        with pytest.raises(TransitionError, match="verified stakeholder"):
            knowledge.transition_requirement(
                s, client_id, r.id, ReqStatus.CONFIRMED, "cross-client"
            )
        assert (
            s.scalars(select(Stakeholder).where(Stakeholder.id == foreign.id)).one().client_id
            == other
        )


def _meeting(s, client_id):
    m = Meeting(client_id=client_id, title="t", source="x")
    s.add(m)
    s.flush()
    return m.id
