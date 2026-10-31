import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.domain.audit import GENESIS, compute_hash, verify_chain
from app.persistence.models import AuditEvent


def chain_key_for(client_id: uuid.UUID | None) -> str:
    return str(client_id) if client_id else "system"


_PENDING = "pending_audit"


def append_audit(
    session: Session,
    event_type: str,
    payload: Mapping[str, Any],
    client_id: uuid.UUID | None = None,
) -> None:
    """Record an audit event in the caller's transaction.

    The event is buffered and written into the client's hash chain just before COMMIT (see
    `_before_commit`). The per-chain advisory lock is therefore held for milliseconds, not for the
    length of a job: a long analysis (LLM calls inside one transaction) must never block live-call
    requests that also write audit events for the same client. A rolled-back transaction drops its
    events, exactly as before.
    """
    session.info.setdefault(_PENDING, []).append((event_type, dict(payload), client_id))


def materialize_audit(session: Session) -> None:
    """Write buffered events into their chains, in order. Takes the chain lock until commit."""
    pending: list[tuple[str, dict[str, Any], uuid.UUID | None]] = session.info.pop(_PENDING, [])
    for event_type, data, client_id in pending:
        key = chain_key_for(client_id)
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
        last = session.execute(
            select(AuditEvent.seq, AuditEvent.hash)
            .where(AuditEvent.chain_key == key)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        ).first()
        seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
        session.add(
            AuditEvent(
                chain_key=key,
                client_id=client_id,
                seq=seq,
                event_type=event_type,
                payload=data,
                prev_hash=prev,
                hash=compute_hash(prev, event_type, data),
            )
        )
        session.flush()


@event.listens_for(Session, "before_commit")
def _before_commit(session: Session) -> None:
    materialize_audit(session)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


def verify_client_chain(session: Session, client_id: uuid.UUID | None) -> int | None:
    materialize_audit(session)
    key = chain_key_for(client_id)
    rows = session.execute(
        select(AuditEvent.event_type, AuditEvent.prev_hash, AuditEvent.payload, AuditEvent.hash)
        .where(AuditEvent.chain_key == key)
        .order_by(AuditEvent.seq)
    ).all()
    return verify_chain((r[0], r[1], r[2], r[3]) for r in rows)


def count_events(session: Session, client_id: uuid.UUID | None) -> int:
    materialize_audit(session)
    return (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.chain_key == chain_key_for(client_id))
        )
        or 0
    )
