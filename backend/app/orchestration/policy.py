"""Deterministic policy engine. Code decides; an LLM may advise but never decides.

Maps the CLAUDE.md section 9 autonomy matrix to rules. Every decision carries rule IDs and a
reason so it can be persisted (PolicyDecision) and explained.
"""

from dataclasses import dataclass
from enum import StrEnum


class Op(StrEnum):
    CREATE_STORY_TASK = "create_story_task"
    CREATE_EPIC = "create_epic"
    UPDATE_TEXT = "update_text"
    ADD_REFERENCE = "add_reference"
    RESOLVE_CONFLICT = "resolve_conflict"
    CHANGE_SPRINT_RELEASE = "change_sprint_release"
    DELETE_ISSUE = "delete_issue"
    CHANGE_PERMISSIONS = "change_permissions"


class Outcome(StrEnum):
    EXECUTE = "execute"
    CLARIFY = "clarify"
    ESCALATE = "escalate"
    REJECT = "reject"


class Mode(StrEnum):
    DRY_RUN = "dry_run"
    PROPOSE = "propose"
    AUTONOMOUS = "autonomous"


_MODE_ORDER = {Mode.DRY_RUN: 0, Mode.PROPOSE: 1, Mode.AUTONOMOUS: 2}

PROHIBITED = {Op.CHANGE_SPRINT_RELEASE, Op.DELETE_ISSUE, Op.CHANGE_PERMISSIONS}
_NEEDS_READY = {Op.CREATE_STORY_TASK, Op.CREATE_EPIC, Op.UPDATE_TEXT}


@dataclass(frozen=True)
class PolicyInput:
    op: Op
    kill_switch: bool = False
    project_allowlisted: bool = False
    requirement_status: str = ""
    evidence_present: bool = False
    ac_measurable: bool = False
    open_blocking_questions: int = 0
    open_conflicts: int = 0
    duplicate_candidates: int = 0
    draft_valid: bool = False
    hierarchy_ok: bool = False  # epics only: type exists and scope is clear
    change_clearly_evidenced: bool = False  # updates only
    target_unambiguous: bool = False  # references/updates only


@dataclass(frozen=True)
class PolicyResult:
    outcome: Outcome
    rule_ids: tuple[str, ...]
    reason: str


def stricter_mode(global_mode: str, project_mode: str | None) -> Mode:
    g = Mode(global_mode)
    if not project_mode:
        return g
    p = Mode(project_mode)
    return g if _MODE_ORDER[g] <= _MODE_ORDER[p] else p


def evaluate(p: PolicyInput) -> PolicyResult:
    """First matching rule wins; ordered from most to least restrictive."""
    if p.op in PROHIBITED:
        return PolicyResult(Outcome.REJECT, ("R-PROHIBITED",), f"{p.op} has no code path")
    if p.kill_switch:
        return PolicyResult(Outcome.ESCALATE, ("R-KILL-SWITCH",), "kill switch is on: writes held")
    if not p.project_allowlisted:
        return PolicyResult(Outcome.REJECT, ("R-ALLOWLIST",), "Jira project is not allowlisted")
    if p.op is Op.RESOLVE_CONFLICT:
        return PolicyResult(
            Outcome.ESCALATE,
            ("R-CONFLICT-NEVER-AUTO",),
            "conflicts are never resolved automatically",
        )
    if not p.evidence_present:
        return PolicyResult(Outcome.REJECT, ("R-EVIDENCE",), "no evidence linked to this action")
    if p.op in _NEEDS_READY:
        if p.requirement_status != "ready_for_delivery":
            return PolicyResult(
                Outcome.CLARIFY,
                ("R-STATUS",),
                f"requirement is {p.requirement_status!r}, not ready_for_delivery",
            )
        if not p.draft_valid:
            return PolicyResult(
                Outcome.REJECT, ("R-DRAFT-INVALID",), "draft fails project schema validation"
            )
        if not p.ac_measurable:
            return PolicyResult(
                Outcome.CLARIFY, ("R-AC",), "acceptance criteria are not measurable"
            )
    if p.open_blocking_questions > 0:
        return PolicyResult(
            Outcome.CLARIFY,
            ("R-BLOCKING-Q",),
            f"{p.open_blocking_questions} blocking question(s) open",
        )
    if p.open_conflicts > 0:
        return PolicyResult(
            Outcome.ESCALATE, ("R-OPEN-CONFLICT",), "open conflict with an approved rule"
        )
    if p.op in {Op.CREATE_STORY_TASK, Op.CREATE_EPIC} and p.duplicate_candidates > 0:
        return PolicyResult(
            Outcome.ESCALATE, ("R-DUPLICATE",), "possible duplicate issue exists in Jira"
        )
    if p.op is Op.CREATE_EPIC and not p.hierarchy_ok:
        return PolicyResult(
            Outcome.CLARIFY, ("R-EPIC-HIERARCHY",), "epic requires clear hierarchy and scope"
        )
    if p.op is Op.UPDATE_TEXT and not p.change_clearly_evidenced:
        return PolicyResult(
            Outcome.CLARIFY, ("R-UPDATE-EVIDENCE",), "update needs a clear evidenced change"
        )
    if p.op in {Op.UPDATE_TEXT, Op.ADD_REFERENCE} and not p.target_unambiguous:
        return PolicyResult(
            Outcome.ESCALATE, ("R-AMBIGUOUS-TARGET",), "target issue is ambiguous or unauthorized"
        )
    return PolicyResult(Outcome.EXECUTE, ("R-PASS",), "all checks passed")


class Disposition(StrEnum):
    DRY_RUN = "dry_run"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTE_NOW = "execute_now"
    HELD = "held"
    CLARIFY = "clarify"
    REJECTED = "rejected"


def disposition(result: PolicyResult, mode: Mode) -> Disposition:
    """Combine the policy outcome with the autonomy mode into what happens to the intent."""
    if result.outcome is Outcome.REJECT:
        return Disposition.REJECTED
    if result.outcome is Outcome.ESCALATE:
        return Disposition.HELD
    if result.outcome is Outcome.CLARIFY:
        return Disposition.CLARIFY
    return {
        Mode.DRY_RUN: Disposition.DRY_RUN,
        Mode.PROPOSE: Disposition.AWAITING_APPROVAL,
        Mode.AUTONOMOUS: Disposition.EXECUTE_NOW,
    }[mode]
