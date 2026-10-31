import itertools

import pytest

from app.domain.requirement import (
    ALLOWED,
    ReqStatus,
    TransitionContext,
    TransitionError,
    check_transition,
)

FULL = TransitionContext(
    has_evidence=True,
    authority_verified=True,
    acceptance_criteria_measurable=True,
    open_blocking_questions=0,
)

ALLOWED_PAIRS = [(a, b) for a, targets in ALLOWED.items() for b in targets]
DISALLOWED_PAIRS = [
    (a, b) for a, b in itertools.product(ReqStatus, ReqStatus) if b not in ALLOWED[a]
]


@pytest.mark.parametrize(("a", "b"), ALLOWED_PAIRS)
def test_every_allowed_transition_passes_with_full_context(a, b):
    check_transition(a, b, FULL)


@pytest.mark.parametrize(("a", "b"), DISALLOWED_PAIRS)
def test_every_disallowed_transition_is_rejected(a, b):
    with pytest.raises(TransitionError):
        check_transition(a, b, FULL)


def test_confirm_requires_verified_authority():
    ctx = TransitionContext(has_evidence=True, authority_verified=False)
    with pytest.raises(TransitionError, match="verified stakeholder"):
        check_transition(ReqStatus.UNDER_CLARIFICATION, ReqStatus.CONFIRMED, ctx)


def test_confirm_requires_evidence():
    ctx = TransitionContext(has_evidence=False, authority_verified=True)
    with pytest.raises(TransitionError, match="evidence"):
        check_transition(ReqStatus.EXTRACTED, ReqStatus.CONFIRMED, ctx)


def test_confirm_blocked_by_conflict_with_approved_rule():
    ctx = TransitionContext(
        has_evidence=True, authority_verified=True, has_conflict_with_approved_rule=True
    )
    with pytest.raises(TransitionError, match="conflict"):
        check_transition(ReqStatus.EXTRACTED, ReqStatus.CONFIRMED, ctx)


def test_ready_requires_measurable_ac_and_no_blocking_questions():
    with pytest.raises(TransitionError, match="acceptance criteria"):
        check_transition(
            ReqStatus.CONFIRMED,
            ReqStatus.READY_FOR_DELIVERY,
            TransitionContext(has_evidence=True, authority_verified=True),
        )
    with pytest.raises(TransitionError, match="blocking questions"):
        check_transition(
            ReqStatus.CONFIRMED,
            ReqStatus.READY_FOR_DELIVERY,
            TransitionContext(acceptance_criteria_measurable=True, open_blocking_questions=1),
        )


def test_extracted_requires_evidence():
    with pytest.raises(TransitionError, match="evidence"):
        check_transition(ReqStatus.OBSERVED, ReqStatus.EXTRACTED, TransitionContext())


def test_terminal_states_have_no_exits():
    for s in (ReqStatus.REJECTED, ReqStatus.CANCELLED, ReqStatus.SUPERSEDED):
        assert ALLOWED[s] == set()
