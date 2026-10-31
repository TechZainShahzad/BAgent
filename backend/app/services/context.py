"""Persistent product understanding for follow-up calls.

Builds a compact, trusted summary of what the platform already knows about a client (and
project): earlier calls, requirements with their Jira keys, open questions and the topics already
covered. Everything is scoped by client_id (and project_id when given). Pure database reads.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.interviewer import TOPICS, PriorKnowledge
from app.persistence.models import (
    JiraIssueMapping,
    Meeting,
    OpenQuestion,
    Project,
    Requirement,
)

_HIDDEN = ("rejected", "cancelled", "superseded")
_DONE_MEETING = ("analyzed", "queued", "analyzing")


def _clip(text: str, n: int = 170) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "..."


def build_prior(
    session: Session,
    client_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    exclude_meeting_id: uuid.UUID | None = None,
) -> PriorKnowledge | None:
    """Returns None for a brand-new client (the agent then gives the normal first-call opening)."""
    meetings_q = select(Meeting).where(
        Meeting.client_id == client_id, Meeting.status.in_(_DONE_MEETING)
    )
    if exclude_meeting_id:
        meetings_q = meetings_q.where(Meeting.id != exclude_meeting_id)
    if project_id:
        meetings_q = meetings_q.where(Meeting.project_id == project_id)
    meetings = list(session.scalars(meetings_q.order_by(Meeting.created_at.desc())))
    reqs_q = select(Requirement).where(
        Requirement.client_id == client_id, Requirement.status.notin_(_HIDDEN)
    )
    if exclude_meeting_id:
        reqs_q = reqs_q.where(
            (Requirement.meeting_id != exclude_meeting_id) | (Requirement.meeting_id.is_(None))
        )
    if project_id:
        reqs_q = reqs_q.where(Requirement.project_id == project_id)
    reqs = list(session.scalars(reqs_q.order_by(Requirement.created_at)))
    if not meetings and not reqs:
        return None

    keys: dict[uuid.UUID, str] = {
        m.requirement_id: m.jira_key
        for m in session.scalars(
            select(JiraIssueMapping).where(
                JiraIssueMapping.client_id == client_id, JiraIssueMapping.relation == "primary"
            )
        )
    }
    goal = next((r for r in reqs if r.kind == "goal"), None)
    epic = None
    if goal is not None and goal.id in keys:
        epic = f"{keys[goal.id]}: {_clip(goal.title, 90)}"

    lines = []
    for r in reqs:
        if r.kind == "goal":
            continue
        tail = f" ({keys[r.id]})" if r.id in keys else ""
        lines.append(
            f"[{r.status.replace('_', ' ')}] {_clip(r.title, 70)}: {_clip(r.statement)}{tail}"
        )

    qs_q = select(OpenQuestion).where(
        OpenQuestion.client_id == client_id, OpenQuestion.status.in_(("open", "asked"))
    )
    if exclude_meeting_id:
        qs_q = qs_q.where(OpenQuestion.meeting_id != exclude_meeting_id)
    questions = [
        ("[blocking] " if q.blocking else "") + _clip(q.question, 200)
        for q in session.scalars(
            qs_q.order_by(OpenQuestion.blocking.desc(), OpenQuestion.created_at)
        )
    ]

    covered: set[str] = set()
    for m in meetings:
        covered |= {t for t in (m.interview_state or {}).get("covered", []) if t in TOPICS}

    project = session.get(Project, project_id) if project_id else None
    last = meetings[0] if meetings else None
    return PriorKnowledge(
        calls=len(meetings),
        last_call_title=last.title if last else "(none)",
        last_call_date=last.created_at.strftime("%d %b %Y") if last else "n/a",
        project=(
            f"{project.name} (Jira {project.jira_project_key})"
            if project and project.jira_project_key
            else (project.name if project else None)
        ),
        epic=epic,
        goal=_clip(goal.title, 90) if goal else None,
        requirements=tuple(lines),
        open_questions=tuple(questions),
        covered_topics=tuple(sorted(covered)),
    )
