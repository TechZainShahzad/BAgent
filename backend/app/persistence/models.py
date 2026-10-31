import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_now)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = _ts()


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("client_id", "name"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    jira_project_key: Mapped[str | None] = mapped_column(String(50))
    autonomy_mode: Mapped[str | None] = mapped_column(String(20))
    # True when the platform created this project in Jira itself (allowed automatically)
    jira_provisioned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("client_id", "kind", "event_id", name="uq_job_event"),
        Index("ix_jobs_claim", "status", "run_after"),
    )
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    kind: Mapped[str] = mapped_column(String(100))
    event_id: Mapped[str] = mapped_column(String(200))
    correlation_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # pending | running | succeeded | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = _ts()
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class AuditEvent(Base):
    """Append-only, hash-chained. UPDATE/DELETE are blocked by a DB trigger."""

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("chain_key", "seq", name="uq_audit_chain_seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chain_key: Mapped[str] = mapped_column(String(80), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    subject_type: Mapped[str] = mapped_column(String(50))
    subject_id: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20))  # execute|clarify|escalate|reject
    rule_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id"), index=True)
    agent: Mapped[str] = mapped_column(String(100))
    template_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer)
    exit_class: Mapped[str] = mapped_column(String(50))  # ok | <typed error name>
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    correlation_id: Mapped[str] = mapped_column(String(100), default="-")
    created_at: Mapped[datetime] = _ts()


class Stakeholder(Base):
    __tablename__ = "stakeholders"
    __table_args__ = (UniqueConstraint("client_id", "name"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    has_decision_authority: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set only by a human; never inferred from a transcript label.
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(50))  # transcript_file | local_capture | ...
    # created | queued | analyzing | analyzed | failed | degraded
    status: Mapped[str] = mapped_column(String(20), default="created")
    consent_status: Mapped[str] = mapped_column(String(20), default="none")  # none | recorded
    failure_reason: Mapped[str | None] = mapped_column(Text)
    transcript_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # live capture (Copilot): heartbeat + degradation are visible state, never hidden
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_segment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_question_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    degraded_reason: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # interview mode: the agent speaks. State holds checklist coverage + the pending utterance.
    mode: Mapped[str] = mapped_column(String(20), default="copilot")  # copilot | interview
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False)
    interview_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    remote_stakeholder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stakeholders.id"))
    created_at: Mapped[datetime] = _ts()


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"), index=True)
    basis: Mapped[str] = mapped_column(Text)  # who attested what, e.g. "operator attests ..."
    recorded_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = _ts()


class TranscriptSegment(Base):
    """Short-lived: purged right after analysis (TRANSCRIPT_GRACE_HOURS, default 0)."""

    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("meeting_id", "idx"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(String(200))
    start_s: Mapped[float | None] = mapped_column(Float)
    end_s: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(20))  # mic | loopback | None (file)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meetings.id"))
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    explicit: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(30), default="extracted")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _ts()


class RequirementVersion(Base):
    """Immutable history. Changes create a new row; old rows are never edited."""

    __tablename__ = "requirement_versions"
    __table_args__ = (UniqueConstraint("requirement_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    change_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class Evidence(Base):
    """Approved minimal excerpt. Only created after the Grounding Verifier passed."""

    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"), index=True)
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    question_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("open_questions.id"))
    segment_index: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    speaker_label: Mapped[str | None] = mapped_column(String(200))  # as labelled in the source
    stakeholder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stakeholders.id"))
    attribution_confidence: Mapped[float | None] = mapped_column(Float)  # None = not asserted
    # source_label (as written in source) | operator_declared (human asserted at capture start)
    attribution_source: Mapped[str] = mapped_column(String(30), default="source_label")
    # confirm-back: the client's affirmative that makes an assistant proposal evidence
    confirmation_quote: Mapped[str | None] = mapped_column(Text)
    start_s: Mapped[float | None] = mapped_column(Float)
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = _ts()


class OpenQuestion(Base):
    __tablename__ = "open_questions"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meetings.id"))
    question: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    # A blocking question only blocks the specific requirements that cannot be built without it.
    blocks_requirement_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | answered | dropped
    created_at: Mapped[datetime] = _ts()


class PurgeRun(Base):
    __tablename__ = "purge_runs"
    id: Mapped[uuid.UUID] = _pk()
    ran_at: Mapped[datetime] = _ts()
    evidence_deleted: Mapped[int] = mapped_column(Integer, default=0)
    segments_deleted: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeChunk(Base):
    """Scoped searchable fragment with embedding and provenance. Derived data: expires with its
    source evidence and is deleted by the retention purge."""

    __tablename__ = "knowledge_chunks"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(30))  # requirement | decision | ...
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meetings.id"))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(384))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)  # False = superseded
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = _ts()


class ConflictRecord(Base):
    """Duplicate / contradiction / refinement found between two requirements. Never auto-resolved."""

    __tablename__ = "conflict_records"
    __table_args__ = (UniqueConstraint("requirement_id", "other_requirement_id", "relation"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"), index=True)
    other_requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"))
    relation: Mapped[str] = mapped_column(String(20))  # duplicate | contradiction | refines
    similarity: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    # open | resolved | dismissed ; resolution is a human/escalation decision, never automatic
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = _ts()


class SystemFlag(Base):
    """Runtime switches (kill switch). Env value is only the default; DB wins when present."""

    __tablename__ = "system_flags"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_by: Mapped[str | None] = mapped_column(String(320))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JiraSchemaSnapshot(Base):
    """Discovered Jira project configuration. Never assume; refreshed by command."""

    __tablename__ = "jira_schema_snapshots"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    project_key: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = _ts()


class StoryDraft(Base):
    __tablename__ = "story_drafts"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"), index=True)
    requirement_version: Mapped[int] = mapped_column(Integer)
    project_key: Mapped[str] = mapped_column(String(50))
    issue_type: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, default=list)
    parent_key: Mapped[str | None] = mapped_column(String(50))
    review_flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ac_measurable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Technical BA contribution, always labelled as assistant-suggested (never client statements)
    technical_notes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _ts()


class ActionIntent(Base):
    """Durable, idempotent external action. Persisted BEFORE any side effect."""

    __tablename__ = "action_intents"
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirements.id"))
    draft_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("story_drafts.id"))
    kind: Mapped[str] = mapped_column(String(30))  # create_issue | update_issue | add_reference
    # dry_run | awaiting_approval | approved | executing | unknown_outcome | succeeded |
    # failed | rejected | cancelled | held
    status: Mapped[str] = mapped_column(String(30), default="approved", index=True)
    project_key: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    marker: Mapped[str] = mapped_column(String(80), unique=True)
    jira_key: Mapped[str | None] = mapped_column(String(50))
    jira_id: Mapped[str | None] = mapped_column(String(50))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_decisions.id"))
    approved_by: Mapped[str | None] = mapped_column(String(320))
    prior_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class JiraIssueMapping(Base):
    __tablename__ = "jira_issue_mappings"
    __table_args__ = (UniqueConstraint("client_id", "jira_key"),)
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirements.id"), index=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("action_intents.id"))
    project_key: Mapped[str] = mapped_column(String(50))
    jira_key: Mapped[str] = mapped_column(String(50))
    jira_id: Mapped[str] = mapped_column(String(50))
    relation: Mapped[str] = mapped_column(String(20), default="primary")  # primary | reference
    last_synced_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _ts()


class ConversationMessage(Base):
    """The retained, human-readable record of a call: who said what, in order.

    Separate from the short-lived working transcript (transcript_segments, purged right after
    analysis). Kept for CONVERSATION_RETENTION_DAYS (0 = not stored), then deleted by the
    retention job. Written only for meetings with a recorded consent. Audio is never stored.
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("meeting_id", "idx"),
        Index("ix_conv_meeting_idx", "meeting_id", "idx"),
    )
    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    # agent = the AI BA | client = remote voice (loopback) | operator = local mic | participant = file
    role: Mapped[str] = mapped_column(String(20))
    speaker: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(20))
    start_s: Mapped[float | None] = mapped_column(Float)
    said_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
