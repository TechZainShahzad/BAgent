"""Grounding Verifier: an evidence quote must be verbatim (whitespace-normalized) in the
cited segment. Deterministic; this is what makes fabricated evidence impossible to persist."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

MIN_QUOTE_CHARS = 8
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class GroundingResult:
    ok: bool
    reason: str | None = None


def verify_quote(quote: str, segment_texts: Sequence[str], segment_index: int) -> GroundingResult:
    if not 0 <= segment_index < len(segment_texts):
        return GroundingResult(False, f"segment_index {segment_index} does not exist")
    q = normalize(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return GroundingResult(False, f"quote shorter than {MIN_QUOTE_CHARS} characters")
    if q not in normalize(segment_texts[segment_index]):
        return GroundingResult(False, "quote is not verbatim in the cited segment")
    return GroundingResult(True)


# Confirm-back: a client's short affirmative to an assistant proposal is valid evidence for the
# proposal ONLY if it is a deterministic affirmative that immediately follows the proposal.
_AFFIRM = re.compile(
    r"^\W*(yes|yeah|yep|yup|correct|right|exactly|sure|absolutely|definitely|ok|okay|"
    r"sounds good|that'?s (right|correct|fine|good|perfect)|that is (right|correct|fine|good))\b",
    re.I,
)
_NEGATION_TAIL = re.compile(r"\b(but|except|however|not quite|no)\b", re.I)


def is_affirmative(text: str) -> bool:
    """True for a clear yes. 'Yes but ...', 'No' or hedged answers are NOT confirmations."""
    t = normalize(text)
    return bool(_AFFIRM.match(t)) and not _NEGATION_TAIL.search(t)


def verify_confirmed_proposal(
    quote: str,
    proposal_index: int,
    confirmation_index: int,
    segment_texts: Sequence[str],
    segment_is_agent: Sequence[bool],
) -> GroundingResult:
    n = len(segment_texts)
    if not (0 <= proposal_index < n and 0 <= confirmation_index < n):
        return GroundingResult(False, "segment index does not exist")
    if not segment_is_agent[proposal_index]:
        return GroundingResult(False, "proposal segment is not an assistant segment")
    if segment_is_agent[confirmation_index]:
        return GroundingResult(False, "confirmation must come from the client, not the assistant")
    if not 1 <= confirmation_index - proposal_index <= 2:
        return GroundingResult(False, "confirmation must immediately follow the proposal")
    if any(segment_is_agent[proposal_index + 1 : confirmation_index]):
        return GroundingResult(False, "another assistant turn intervenes before the confirmation")
    if not is_affirmative(segment_texts[confirmation_index]):
        return GroundingResult(False, "client reply is not a clear affirmative")
    return verify_quote(quote, segment_texts, proposal_index)
