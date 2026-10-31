"""Durable PostgreSQL job queue: SKIP LOCKED claims, bounded retries, poison -> failed."""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.orchestration.audit import append_audit
from app.persistence.models import Job

Handler = Callable[[Job], Awaitable[None]]

BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 300.0


def enqueue(
    session: Session,
    client_id: uuid.UUID,
    kind: str,
    event_id: str,
    payload: Mapping[str, Any] | None = None,
    correlation_id: str | None = None,
    max_attempts: int = 3,
) -> tuple[uuid.UUID | None, bool]:
    """Persist a job. Returns (job_id, created). Duplicate (client, kind, event_id) is ignored."""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(Job)
        .values(
            id=uuid.uuid4(),
            client_id=client_id,
            kind=kind,
            event_id=event_id,
            correlation_id=correlation_id or uuid.uuid4().hex,
            payload=dict(payload or {}),
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
            run_after=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_job_event")
        .returning(Job.id)
    )
    job_id = session.execute(stmt).scalar()
    if job_id is not None:
        append_audit(session, "job.enqueued", {"job_id": str(job_id), "kind": kind}, client_id)
    return job_id, job_id is not None


def claim(
    session: Session,
    worker_id: str,
    kinds: list[str] | None = None,
    client_id: uuid.UUID | None = None,
) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.status == "pending", Job.run_after <= datetime.now(UTC))
        .order_by(Job.run_after)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if kinds is not None:
        stmt = stmt.where(Job.kind.in_(kinds))
    if client_id is not None:
        stmt = stmt.where(Job.client_id == client_id)
    job = session.scalars(stmt).first()
    if job is None:
        return None
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = datetime.now(UTC)
    job.attempts += 1
    session.flush()
    return job


def mark_succeeded(session: Session, job: Job) -> None:
    job.status = "succeeded"
    job.locked_by = None
    job.last_error = None
    append_audit(session, "job.succeeded", {"job_id": str(job.id), "kind": job.kind}, job.client_id)


def mark_failed_attempt(session: Session, job: Job, error: str) -> None:
    job.last_error = error[:2000]
    job.locked_by = None
    if job.attempts >= job.max_attempts:
        job.status = "failed"
        append_audit(
            session,
            "job.failed",
            {
                "job_id": str(job.id),
                "kind": job.kind,
                "attempts": job.attempts,
                "error": error[:500],
            },
            job.client_id,
        )
    else:
        delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S**job.attempts)
        job.status = "pending"
        job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
        append_audit(
            session,
            "job.retry_scheduled",
            {"job_id": str(job.id), "attempt": job.attempts, "delay_s": delay},
            job.client_id,
        )


def recover_stale(session: Session, stale_after: timedelta = timedelta(minutes=10)) -> int:
    """Startup/periodic resume: jobs left 'running' by a dead worker go back to pending."""
    cutoff = datetime.now(UTC) - stale_after
    result = session.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < cutoff)
        .values(status="pending", locked_by=None, run_after=datetime.now(UTC))
    )
    count = result.rowcount or 0  # type: ignore[attr-defined]
    if count:
        append_audit(session, "job.recovered_stale", {"count": count})
    return count
