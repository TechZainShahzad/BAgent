"""Testability Reviewer (deterministic): are acceptance criteria measurable and unambiguous?

Gates ready_for_delivery. Conservative about vague wording; accepts any criterion that states an
observable outcome (active, passive or negated) or a measurable value.
"""

import re
from dataclasses import dataclass

VAGUE_TERMS = [
    "fast",
    "quick",
    "quickly",
    "easy",
    "easily",
    "user-friendly",
    "user friendly",
    "intuitive",
    "simple",
    "seamless",
    "robust",
    "scalable",
    "efficient",
    "better",
    "improved",
    "appropriate",
    "reasonable",
    "as needed",
    "etc",
    "and so on",
    "flexible",
    "modern",
    "secure enough",
]
_HAS_NUMBER = re.compile(r"\d")
_OBSERVABLE = re.compile(
    r"\b(shows?|shown|displays?|displayed|returns?|returned|sends?|sent|redirects?|rejects?|"
    r"rejected|accepts?|accepted|creates?|created|expires?|expired|logs?|logged|records?|"
    r"recorded|blocks?|blocked|allows?|allowed|prevents?|prevented|within|at least|at most|"
    r"no more than|must not|cannot|only|contains?|contained|exports?|exported|lists?|listed|"
    r"requires?|required|approves?|approved|stores?|stored|deletes?|deleted|included?|"
    r"excluded?|hidden|visible|appears?|available|unavailable|sorted|ordered|matches?|"
    r"persists?|updated|disabled|enabled|notified|validated)\b",
    re.I,
)
# passive / negated outcomes: "are not shown", "is notified", "will be deleted"
_PASSIVE = re.compile(r"\b(is|are|be|been|was|were)\s+(not\s+)?\w+(ed|en)\b", re.I)
# capability / obligation statements: "A signed-in parent can view ...", "must not ...", "sees ..."
_MODAL = re.compile(
    r"\b(can|cannot|can't|could not|must|must not|mustn't|will|will not|won't|shall|should|"
    r"should not|does not|do not|doesn't|don't|is able to|are able to|sees?|views?|enters?|"
    r"opens?|selects?|chooses?|pays?|receives?|gets?|identif(y|ies)|remains?|continues?)\b",
    re.I,
)
MIN_WORDS = 5


@dataclass(frozen=True)
class Review:
    measurable: bool
    flags: list[str]


def review_acceptance_criteria(criteria: list[str]) -> Review:
    flags: list[str] = []
    if not criteria:
        return Review(False, ["no acceptance criteria"])
    for i, ac in enumerate(criteria, 1):
        text = ac.strip()
        low = text.lower()
        if len(text.split()) < MIN_WORDS:
            flags.append(f"AC{i}: too short to be testable")
        for term in VAGUE_TERMS:
            if re.search(rf"\b{re.escape(term)}\b", low) and not _HAS_NUMBER.search(text):
                flags.append(f"AC{i}: vague term {term!r} without a measurable value")
        if not (
            _OBSERVABLE.search(text)
            or _PASSIVE.search(text)
            or _MODAL.search(text)
            or _HAS_NUMBER.search(text)
        ):
            flags.append(f"AC{i}: no observable outcome or measurable value")
    return Review(not flags, flags)
