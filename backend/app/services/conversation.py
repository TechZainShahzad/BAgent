"""The written record of a call (who said what), kept for CONVERSATION_RETENTION_DAYS.

This is deliberately separate from the working transcript: that one is deleted right after
analysis; this one is the human-readable history shown in the dashboard's chat view.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.persistence.models import ConversationMessage, Meeting

ROLE_BY_CHANNEL = {"agent": "agent", "mic": "operator", "loopback": "client"}


def role_for(channel: str | None) -> str:
    return ROLE_BY_CHANNEL.get(channel or "", "participant")


def retention_days() -> int:
    return get_settings().conversation_retention_days


def record_message(
    session: Session,
    client_id: uuid.UUID,
    meeting_id: uuid.UUID,
    idx: int,
    channel: str | None,
    speaker: str | None,
    text: str,
    start_s: float | None = None,
    now: datetime | None = None,
) -> ConversationMessage | None:
    """Store one message. Returns None when conversation storage is switched off (0 days)."""
    days = retention_days()
    if days <= 0 or not text.strip():
        return None
    said = now or datetime.now(UTC)
    msg = ConversationMessage(
        client_id=client_id,
        meeting_id=meeting_id,
        idx=idx,
        role=role_for(channel),
        speaker=speaker,
        text=text.strip(),
        channel=channel,
        start_s=start_s,
        said_at=said,
        expires_at=said + timedelta(days=days),
    )
    session.add(msg)
    return msg


def list_conversations(session: Session, client_id: uuid.UUID) -> list[dict[str, object]]:
    """One row per meeting with message counts and time span (newest call first)."""
    counts = {
        row.meeting_id: row
        for row in session.execute(
            select(
                ConversationMessage.meeting_id,
                func.count().label("n"),
                func.count().filter(ConversationMessage.role == "agent").label("agent"),
                func.min(ConversationMessage.said_at).label("first"),
                func.max(ConversationMessage.said_at).label("last"),
            )
            .where(ConversationMessage.client_id == client_id)
            .group_by(ConversationMessage.meeting_id)
        )
    }
    out: list[dict[str, object]] = []
    for m in session.scalars(
        select(Meeting).where(Meeting.client_id == client_id).order_by(Meeting.created_at.desc())
    ):
        c = counts.get(m.id)
        n = int(c.n) if c else 0
        agent = int(c.agent) if c else 0
        out.append(
            {
                "meeting_id": str(m.id),
                "title": m.title,
                "status": m.status,
                "source": m.source,
                "mode": m.mode,
                "started_at": m.created_at.isoformat(),
                "ended_at": m.ended_at.isoformat() if m.ended_at else None,
                "messages": n,
                "agent_messages": agent,
                "other_messages": n - agent,
                "first_at": c.first.isoformat() if c else None,
                "last_at": c.last.isoformat() if c else None,
            }
        )
    return out


def get_conversation(
    session: Session, client_id: uuid.UUID, meeting_id: uuid.UUID, after: int = -1
) -> dict[str, object]:
    m = session.get(Meeting, meeting_id)
    if m is None or m.client_id != client_id:
        raise LookupError("meeting not found in client scope")
    rows = session.scalars(
        select(ConversationMessage)
        .where(
            ConversationMessage.meeting_id == m.id,
            ConversationMessage.client_id == client_id,
            ConversationMessage.idx > after,
        )
        .order_by(ConversationMessage.idx)
    )
    total = (
        session.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.meeting_id == m.id, ConversationMessage.client_id == client_id
            )
        )
        or 0
    )
    return {
        "meeting": {
            "id": str(m.id),
            "title": m.title,
            "status": m.status,
            "source": m.source,
            "mode": m.mode,
            "started_at": m.created_at.isoformat(),
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
        },
        "total": int(total),
        "messages": [
            {
                "idx": r.idx,
                "role": r.role,
                "speaker": r.speaker,
                "text": r.text,
                "channel": r.channel,
                "start_s": r.start_s,
                "said_at": r.said_at.isoformat(),
            }
            for r in rows
        ],
    }
