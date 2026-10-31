"""Explainer: renders the stored decision trace for a requirement. No LLM, so it cannot
hallucinate reasons: everything shown here is read from persisted rows."""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ActionIntent,
    ConflictRecord,
    Evidence,
    JiraIssueMapping,
    LLMCallLog,
    PolicyDecision,
    Requirement,
    RequirementVersion,
    StoryDraft,
)


def explain_requirement(
    session: Session, client_id: uuid.UUID, requirement_id: uuid.UUID
) -> dict[str, Any]:
    req = session.get(Requirement, requirement_id)
    if req is None or req.client_id != client_id:
        raise LookupError("requirement not found in client scope")

    def q(model: Any) -> Select[Any]:
        return select(model).where(model.client_id == client_id)

    evidence = session.scalars(q(Evidence).where(Evidence.requirement_id == req.id))
    versions = session.scalars(
        q(RequirementVersion)
        .where(RequirementVersion.requirement_id == req.id)
        .order_by(RequirementVersion.version)
    )
    conflicts = session.scalars(
        q(ConflictRecord).where(
            (ConflictRecord.requirement_id == req.id)
            | (ConflictRecord.other_requirement_id == req.id)
        )
    )
    drafts = session.scalars(
        q(StoryDraft).where(StoryDraft.requirement_id == req.id).order_by(StoryDraft.created_at)
    )
    intents = list(session.scalars(q(ActionIntent).where(ActionIntent.requirement_id == req.id)))
    decisions = session.scalars(
        q(PolicyDecision)
        .where(
            PolicyDecision.subject_type == "requirement", PolicyDecision.subject_id == str(req.id)
        )
        .order_by(PolicyDecision.created_at)
    )
    mappings = session.scalars(q(JiraIssueMapping).where(JiraIssueMapping.requirement_id == req.id))
    llm_calls = session.scalars(q(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(20))
    return {
        "requirement": {
            "id": str(req.id),
            "title": req.title,
            "statement": req.statement,
            "kind": req.kind,
            "explicit": req.explicit,
            "status": req.status,
            "version": req.current_version,
        },
        "evidence": [
            {
                "quote": e.quote,
                "speaker_label": e.speaker_label,
                "start_s": e.start_s,
                "stakeholder_linked": e.stakeholder_id is not None,
                "attribution_confidence": e.attribution_confidence,
                "expires_at": e.expires_at.isoformat(),
            }
            for e in evidence
        ],
        "versions": [
            {
                "version": v.version,
                "title": v.title,
                "statement": v.statement,
                "reason": v.change_reason,
            }
            for v in versions
        ],
        "conflicts": [
            {
                "id": str(c.id),
                "relation": c.relation,
                "status": c.status,
                "similarity": round(c.similarity, 3),
                "explanation": c.explanation,
                "other_requirement_id": str(
                    c.other_requirement_id if c.requirement_id == req.id else c.requirement_id
                ),
            }
            for c in conflicts
        ],
        "drafts": [
            {
                "id": str(d.id),
                "issue_type": d.issue_type,
                "summary": d.summary,
                "acceptance_criteria": d.acceptance_criteria,
                "measurable": d.ac_measurable,
                "flags": d.review_flags,
            }
            for d in drafts
        ],
        "policy_decisions": [
            {
                "outcome": d.outcome,
                "rules": d.rule_ids,
                "reason": d.reason,
                "mode": d.inputs.get("mode"),
                "disposition": d.inputs.get("disposition"),
            }
            for d in decisions
        ],
        "intents": [
            {
                "id": str(i.id),
                "kind": i.kind,
                "status": i.status,
                "jira_key": i.jira_key,
                "marker": i.marker,
                "last_error": i.last_error,
                "attempts": i.attempts,
            }
            for i in intents
        ],
        "jira": [{"key": m.jira_key, "relation": m.relation} for m in mappings],
        "recent_llm_calls": [
            {"agent": c.agent, "model": c.model, "duration_ms": c.duration_ms, "exit": c.exit_class}
            for c in llm_calls
        ],
    }
