"""Autopilot: after a meeting, work independently from analysis to Jira.

Steps (idempotent; safe to retry): confirm what the declared sponsor said (all lifecycle guards
still apply) -> Epic for the goal -> Stories linked under that Epic in the backlog. Everything
that cannot proceed is HELD and reported with its reason; nothing is hidden or forced.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.agents.testability import review_acceptance_criteria
from app.config import Settings
from app.domain.requirement import ReqStatus, TransitionError
from app.integrations.jira import adf
from app.integrations.jira.discovery import discover_project
from app.integrations.jira.execution import JiraExecutor, build_executor
from app.orchestration.audit import append_audit
from app.orchestration.jobs import Handler
from app.persistence.models import (
    ActionIntent,
    JiraIssueMapping,
    JiraSchemaSnapshot,
    Job,
    Meeting,
    Project,
    Requirement,
)
from app.providers.llm.base import LLMProvider
from app.services import delivery, knowledge
from app.services.jira_projects import effective_allowlist

log = logging.getLogger(__name__)

AUTOPILOT_JOB = "autopilot.deliver"
DELIVERABLE = {"confirmed", "ready_for_delivery"}
STORY_KINDS = {"functional", "rule"}
CONTEXT_KINDS = {"constraint", "nfr", "actor"}
ACTIVE_INTENT = {
    "approved",
    "executing",
    "unknown_outcome",
    "succeeded",
    "awaiting_approval",
    "dry_run",
    "held",
}


def _project_key(session: Session, m: Meeting, settings: Settings) -> str | None:
    if m.project_id:
        p = session.get(Project, m.project_id)
        if p and p.jira_project_key:
            return p.jira_project_key
    keys = sorted(effective_allowlist(session, settings))
    return keys[0] if len(keys) == 1 else None


async def _ensure_schema(
    session: Session, executor: JiraExecutor, settings: Settings, client_id: uuid.UUID, key: str
) -> None:
    try:
        delivery.latest_schema(session, client_id, key)
    except delivery.DeliveryError:
        snap = await discover_project(executor.client, key, effective_allowlist(session, settings))
        session.add(JiraSchemaSnapshot(client_id=client_id, project_key=key, payload=snap))
        session.flush()


def _existing_primary_key(session: Session, req: Requirement) -> str | None:
    m = session.scalars(
        select(JiraIssueMapping).where(
            JiraIssueMapping.client_id == req.client_id,
            JiraIssueMapping.requirement_id == req.id,
            JiraIssueMapping.relation == "primary",
        )
    ).first()
    return m.jira_key if m else None


def _existing_epic_key(
    session: Session, client_id: uuid.UUID, project_key: str, exclude: uuid.UUID | None
) -> str | None:
    """The oldest Epic this platform created in the project (a goal requirement with a Jira key)."""
    q = (
        select(JiraIssueMapping.jira_key)
        .join(Requirement, Requirement.id == JiraIssueMapping.requirement_id)
        .where(
            JiraIssueMapping.client_id == client_id,
            JiraIssueMapping.project_key == project_key,
            JiraIssueMapping.relation == "primary",
            Requirement.kind == "goal",
        )
        .order_by(JiraIssueMapping.created_at)
    )
    if exclude is not None:
        q = q.where(Requirement.id != exclude)
    return session.scalars(q).first()


def _has_active_intent(session: Session, req: Requirement) -> ActionIntent | None:
    return session.scalars(
        select(ActionIntent)
        .where(
            ActionIntent.client_id == req.client_id,
            ActionIntent.requirement_id == req.id,
            ActionIntent.status.in_(ACTIVE_INTENT),
        )
        .order_by(ActionIntent.created_at.desc())
    ).first()


async def _deliver_one(
    session: Session,
    provider: LLMProvider,
    settings: Settings,
    executor: JiraExecutor,
    req: Requirement,
    project_key: str,
    parent_key: str | None,
    extra_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"requirement": req.title, "kind": req.kind}
    done = _existing_primary_key(session, req)
    if done:
        return {**out, "result": "already_in_jira", "jira_key": done}
    prior = _has_active_intent(session, req)
    if prior is not None:
        return {**out, "result": f"intent_exists:{prior.status}", "jira_key": prior.jira_key}
    try:
        draft = await delivery.draft_story(
            session, provider, req.client_id, req.id, project_key, parent_key
        )
        ready = await delivery.make_ready_if_measurable(session, req.client_id, req.id)
        if not ready:
            reasons = draft.review_flags or ["not ready for delivery"]
            return {**out, "result": "held_needs_clarification", "reasons": reasons[:6]}
        prepared = await delivery.prepare_delivery(
            session,
            settings,
            executor,
            req.client_id,
            req.id,
            project_key,
            "autopilot",
            extra_blocks=extra_blocks,
        )
    except TransitionError as exc:  # a lifecycle guard refused: held with its reason, not an error
        return {**out, "result": "held_by_guard", "reasons": [str(exc)]}
    except Exception as exc:  # one bad requirement must not stop the others; it is reported
        log.warning("autopilot item failed", exc_info=True)
        return {**out, "result": "error", "reasons": [f"{type(exc).__name__}: {exc}"[:300]]}
    out["policy"] = f"{prepared.decision.outcome} {prepared.decision.rule_ids}"
    intent = prepared.intent
    if intent is None:
        return {**out, "result": f"held_{prepared.disposition.value}", "reasons": prepared.reasons}
    if intent.status == "approved":
        await executor.execute(session, intent)
    return {
        **out,
        "result": f"intent_{intent.status}",
        "kind_of_action": intent.kind,
        "jira_key": intent.jira_key,
        "reasons": [intent.last_error] if intent.last_error else [],
    }


def make_autopilot_handler(
    provider: LLMProvider, settings: Settings, executor_factory: Any = None
) -> Handler:
    factory = executor_factory or (lambda: build_executor(settings))

    async def handler(job: Job) -> None:
        session = object_session(job)
        assert session is not None
        m = session.get(Meeting, uuid.UUID(job.payload["meeting_id"]))
        if m is None or m.client_id != job.client_id or not m.autopilot:
            return
        client_id = m.client_id
        report: dict[str, Any] = {"confirmed": [], "not_confirmed": [], "delivered": []}
        key = _project_key(session, m, settings)
        if key is None:
            report["error"] = "no Jira project configured for this meeting"
            _save(session, m, report)
            return
        reqs = list(
            session.scalars(
                select(Requirement)
                .where(Requirement.client_id == client_id, Requirement.meeting_id == m.id)
                .order_by(Requirement.created_at)
            )
        )
        for r in reqs:
            if r.status in {ReqStatus.EXTRACTED.value, ReqStatus.UNDER_CLARIFICATION.value}:
                try:
                    knowledge.transition_requirement(
                        session,
                        client_id,
                        r.id,
                        ReqStatus.CONFIRMED,
                        "autopilot: stated by the declared sponsor in the meeting",
                    )
                    report["confirmed"].append(r.title)
                except TransitionError as exc:
                    report["not_confirmed"].append({"requirement": r.title, "reason": str(exc)})
        session.flush()
        executor = factory()
        executor.allowlist = effective_allowlist(session, settings)
        try:
            await _ensure_schema(session, executor, settings, client_id, key)
            goal = next((r for r in reqs if r.kind == "goal" and r.status in DELIVERABLE), None)
            epic_key: str | None = None
            context = [r for r in reqs if r.kind in CONTEXT_KINDS and r.status in DELIVERABLE]
            existing_epic = _existing_epic_key(
                session, client_id, key, exclude=goal.id if goal is not None else None
            )
            if goal is not None and existing_epic is not None:
                # follow-up call: the project already has its Epic. Never create a second one.
                epic_key = existing_epic
                report["delivered"].append(
                    {
                        "requirement": goal.title,
                        "kind": goal.kind,
                        "result": "epic_already_exists",
                        "jira_key": existing_epic,
                        "follow_up": "new stories are attached to the existing epic",
                    }
                )
                for c in context:
                    report["delivered"].append(
                        {
                            "requirement": c.title,
                            "kind": c.kind,
                            "result": "kept_in_knowledge_epic_unchanged",
                        }
                    )
            elif goal is not None:
                res = await _deliver_one(
                    session,
                    provider,
                    settings,
                    executor,
                    goal,
                    key,
                    None,
                    _context_blocks(session, context),
                )
                for c in context:
                    entry: dict[str, Any] = {
                        "requirement": c.title,
                        "kind": c.kind,
                        "result": "included_in_epic_description",
                    }
                    if _needs_target(c):
                        entry["follow_up"] = "no measurable target yet; ask the client for a number"
                    report["delivered"].append(entry)
                report["delivered"].append(res)
                epic_key = res.get("jira_key")
                session.commit()
            elif existing_epic is not None:
                epic_key = existing_epic
                report["note"] = f"stories are attached to the existing epic {existing_epic}"
            else:
                report["note"] = "no goal requirement was captured, so stories have no parent epic"
            for r in reqs:
                if r is goal or r.status not in {"confirmed", "ready_for_delivery"}:
                    continue
                if r.kind not in STORY_KINDS:
                    if goal is None and r.kind in CONTEXT_KINDS:
                        report["delivered"].append(
                            {
                                "requirement": r.title,
                                "kind": r.kind,
                                "result": "context_only_no_epic",
                            }
                        )
                    continue
                res = await _deliver_one(session, provider, settings, executor, r, key, epic_key)
                if epic_key is None and goal is not None:
                    res["warning"] = "epic was not created, story created without parent"
                report["delivered"].append(res)
                session.commit()
        finally:
            await executor.client.aclose()
        _save(session, m, report)

    return handler


def _needs_target(r: Requirement) -> bool:
    """A quality target worded vaguely ('fast', 'easy') with no number still needs a value."""
    review = review_acceptance_criteria(
        [r.statement if len(r.statement.split()) >= 5 else r.statement + " overall"]
    )
    return r.kind == "nfr" and any("vague" in f for f in review.flags)


def _context_blocks(session: Session, context: list[Requirement]) -> list[dict[str, Any]]:
    """Constraints, quality targets and success measures belong in the epic, with evidence."""
    if not context:
        return []
    items: list[str] = []
    for r in context:
        quotes = [e.quote for e in delivery._evidence(session, r)][:1]
        tail = f' (client said: "{quotes[0]}")' if quotes else ""
        todo = " [target not yet measurable]" if _needs_target(r) else ""
        items.append(f"{r.statement}{tail}{todo}")
    return [
        adf.heading("Constraints, quality targets and success measures (from the client)"),
        adf.bullet_list(items),
    ]


def _save(session: Session, m: Meeting, report: dict[str, Any]) -> None:
    st = dict(m.interview_state or {})
    st["autopilot_report"] = report
    m.interview_state = st
    created = [d["jira_key"] for d in report.get("delivered", []) if d.get("jira_key")]
    held = [d for d in report.get("delivered", []) if str(d.get("result", "")).startswith("held")]
    append_audit(
        session,
        "autopilot.completed",
        {
            "meeting_id": str(m.id),
            "confirmed": len(report.get("confirmed", [])),
            "not_confirmed": len(report.get("not_confirmed", [])),
            "jira_issues": created,
            "held": len(held),
        },
        m.client_id,
    )
