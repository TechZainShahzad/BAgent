"""Requirement lifecycle rules. Pure domain logic: no framework imports."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import DomainError


class ReqStatus(StrEnum):
    OBSERVED = "observed"
    EXTRACTED = "extracted"
    UNDER_CLARIFICATION = "under_clarification"
    CONFIRMED = "confirmed"
    READY_FOR_DELIVERY = "ready_for_delivery"
    SYNCED = "synced"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


S = ReqStatus

_TERMINAL = {S.SYNCED, S.REJECTED, S.CANCELLED, S.SUPERSEDED}

ALLOWED: dict[ReqStatus, set[ReqStatus]] = {
    S.OBSERVED: {S.EXTRACTED, S.REJECTED, S.CANCELLED},
    S.EXTRACTED: {
        S.UNDER_CLARIFICATION,
        S.CONFIRMED,
        S.REJECTED,
        S.BLOCKED,
        S.CANCELLED,
        S.SUPERSEDED,
    },
    S.UNDER_CLARIFICATION: {S.CONFIRMED, S.REJECTED, S.BLOCKED, S.CANCELLED, S.SUPERSEDED},
    S.CONFIRMED: {
        S.READY_FOR_DELIVERY,
        S.UNDER_CLARIFICATION,
        S.BLOCKED,
        S.CANCELLED,
        S.SUPERSEDED,
    },
    S.READY_FOR_DELIVERY: {S.SYNCED, S.UNDER_CLARIFICATION, S.BLOCKED, S.CANCELLED, S.SUPERSEDED},
    S.BLOCKED: {S.UNDER_CLARIFICATION, S.CANCELLED, S.SUPERSEDED},
    S.SYNCED: {S.SUPERSEDED},  # a synced requirement can still be superseded by a newer one
    S.REJECTED: set(),
    S.CANCELLED: set(),
    S.SUPERSEDED: set(),
}


class TransitionError(DomainError):
    """The requested lifecycle transition is not allowed or its guard failed."""


@dataclass(frozen=True)
class TransitionContext:
    """Facts the guards need. Callers supply them; the domain never fetches anything."""

    has_evidence: bool = False
    authority_verified: bool = False  # confirming stakeholder is a verified decision maker
    acceptance_criteria_measurable: bool = False
    open_blocking_questions: int = 0
    has_conflict_with_approved_rule: bool = False


def check_transition(current: ReqStatus, target: ReqStatus, ctx: TransitionContext) -> None:
    if target not in ALLOWED[current]:
        raise TransitionError(f"{current} -> {target} is not an allowed transition")
    if target in {S.EXTRACTED, S.CONFIRMED} and not ctx.has_evidence:
        raise TransitionError(f"{target} requires linked evidence")
    if target is S.CONFIRMED:
        if not ctx.authority_verified:
            raise TransitionError("confirmed requires a verified stakeholder with authority")
        if ctx.has_conflict_with_approved_rule:
            raise TransitionError(
                "conflict with an approved rule must be resolved, not overwritten"
            )
    if target is S.READY_FOR_DELIVERY:
        if not ctx.acceptance_criteria_measurable:
            raise TransitionError("ready_for_delivery requires measurable acceptance criteria")
        if ctx.open_blocking_questions > 0:
            raise TransitionError("ready_for_delivery requires no open blocking questions")


def is_terminal(status: ReqStatus) -> bool:
    return status in _TERMINAL and status is not S.SYNCED
