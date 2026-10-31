import pytest

from app.domain.grounding import is_affirmative, verify_confirmed_proposal


@pytest.mark.parametrize(
    "text", ["Yes", "yes, that is right", "Yeah sure", "Okay", "Exactly!", "That's correct"]
)
def test_clear_affirmatives(text):
    assert is_affirmative(text)


@pytest.mark.parametrize(
    "text", ["No", "Yes but not for parents", "Maybe", "Not really", "Know-how", "I think so?", ""]
)
def test_hedged_or_negative_replies_are_not_confirmations(text):
    assert not is_affirmative(text)


TEXTS = [
    "Tell me about the portal.",  # 0 client
    "So parents can see grades after logging in, right?",  # 1 agent
    "Yes, exactly.",  # 2 client
    "And fees?",  # 3 agent
    "Hmm, not sure.",  # 4 client
]
AGENT = [False, True, False, True, False]


def test_confirmed_proposal_is_valid_evidence():
    assert verify_confirmed_proposal(
        "parents can see grades after logging in", 1, 2, TEXTS, AGENT
    ).ok


def test_unconfirmed_or_hedged_proposal_is_rejected():
    r = verify_confirmed_proposal("And fees?", 3, 4, TEXTS, AGENT)
    assert not r.ok and "affirmative" in r.reason


def test_confirmation_must_follow_immediately_and_come_from_client():
    q = "parents can see grades"
    assert not verify_confirmed_proposal(q, 1, 4, TEXTS, AGENT).ok  # too far away
    assert not verify_confirmed_proposal(q, 1, 3, TEXTS, AGENT).ok  # the "confirmer" is the agent
    assert not verify_confirmed_proposal(q, 0, 2, TEXTS, AGENT).ok  # proposal is a client line


def test_fabricated_proposal_quote_is_rejected_even_with_a_yes():
    r = verify_confirmed_proposal("parents pay tuition by card", 1, 2, TEXTS, AGENT)
    assert not r.ok and "verbatim" in r.reason
