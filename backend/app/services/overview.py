"""Cross-client read models for the dashboard's Home screen and the client picker.

Pure database reads. Every number is computed from the same tables the per-client screens use,
so Home can never disagree with them.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ActionIntent,
    Client,
    ConflictRecord,
    ConversationMessage,
    Job,
    Meeting,
    OpenQuestion,
    Requirement,
)


def _by_client(session: Session, model: Any, *where: Any) -> dict[Any, int]:
    q = select(model.client_id, func.count()).group_by(model.client_id)
    for w in where:
        q = q.where(w)
    return {cid: int(n) for cid, n in session.execute(q)}


def _latest(session: Session, col: Any, model: Any) -> dict[Any, datetime]:
    return {
        cid: ts
        for cid, ts in session.execute(
            select(model.client_id, func.max(col)).group_by(model.client_id)
        )
    }


def client_summaries(session: Session) -> list[dict[str, Any]]:
    """One row per client, most recently active first."""
    meetings = _by_client(session, Meeting)
    reqs = _by_client(session, Requirement)
    msgs = _by_client(session, ConversationMessage)
    jira = {
        cid: int(n)
        for cid, n in session.execute(
            select(ActionIntent.client_id, func.count(func.distinct(ActionIntent.jira_key)))
            .where(ActionIntent.status == "succeeded", ActionIntent.jira_key.is_not(None))
            .group_by(ActionIntent.client_id)
        )
    }
    open_q = _by_client(session, OpenQuestion, OpenQuestion.status.in_(("open", "asked")))
    last_meeting = _latest(session, Meeting.created_at, Meeting)
    last_req = _latest(session, Requirement.created_at, Requirement)
    rows = []
    for c in session.scalars(select(Client)):
        times = [t for t in (last_meeting.get(c.id), last_req.get(c.id), c.created_at) if t]
        last = max(times)
        rows.append(
            {
                "id": str(c.id),
                "name": c.name,
                "meetings": meetings.get(c.id, 0),
                "requirements": reqs.get(c.id, 0),
                "messages": msgs.get(c.id, 0),
                "jira_issues": jira.get(c.id, 0),
                "open_questions": open_q.get(c.id, 0),
                "last_activity_at": last.isoformat(),
            }
        )
    rows.sort(key=lambda r: str(r["last_activity_at"]), reverse=True)
    return rows


def global_overview(session: Session) -> dict[str, Any]:
    clients = client_summaries(session)
    names = {c["id"]: c["name"] for c in clients}
    status_counts = {
        s: int(n)
        for s, n in session.execute(
            select(Requirement.status, func.count()).group_by(Requirement.status)
        )
    }
    recent_meetings = []
    for m in session.scalars(select(Meeting).order_by(Meeting.created_at.desc()).limit(8)):
        n = session.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.meeting_id == m.id)
        )
        recent_meetings.append(
            {
                "meeting_id": str(m.id),
                "client_id": str(m.client_id),
                "client_name": names.get(str(m.client_id), "?"),
                "title": m.title,
                "status": m.status,
                "mode": m.mode,
                "started_at": m.created_at.isoformat(),
                "messages": int(n or 0),
            }
        )
    recent_actions = [
        {
            "client_id": str(i.client_id),
            "client_name": names.get(str(i.client_id), "?"),
            "kind": i.kind,
            "status": i.status,
            "jira_key": i.jira_key,
            "summary": i.payload.get("summary"),
            "created_at": i.created_at.isoformat(),
        }
        for i in session.scalars(
            select(ActionIntent).order_by(ActionIntent.created_at.desc()).limit(8)
        )
    ]
    failed_jobs = int(
        session.scalar(select(func.count()).select_from(Job).where(Job.status == "failed")) or 0
    )
    failed_actions = int(
        session.scalar(
            select(func.count()).select_from(ActionIntent).where(ActionIntent.status == "failed")
        )
        or 0
    )
    waiting = int(
        session.scalar(
            select(func.count())
            .select_from(ActionIntent)
            .where(ActionIntent.status.in_(("awaiting_approval", "held")))
        )
        or 0
    )
    open_conflicts = int(
        session.scalar(
            select(func.count())
            .select_from(ConflictRecord)
            .where(ConflictRecord.status == "open", ConflictRecord.relation == "contradiction")
        )
        or 0
    )
    live = int(
        session.scalar(select(func.count()).select_from(Meeting).where(Meeting.status == "live"))
        or 0
    )
    return {
        "totals": {
            "clients": len(clients),
            "meetings": sum(c["meetings"] for c in clients),
            "messages": sum(c["messages"] for c in clients),
            "requirements": sum(c["requirements"] for c in clients),
            "jira_issues": sum(c["jira_issues"] for c in clients),
            "open_questions": sum(c["open_questions"] for c in clients),
            "live_calls": live,
            "failed_jobs": failed_jobs,
            "failed_actions": failed_actions,
            "waiting_actions": waiting,
            "open_contradictions": open_conflicts,
        },
        "requirements_by_status": status_counts,
        "clients": clients,
        "recent_meetings": recent_meetings,
        "recent_actions": recent_actions,
    }
