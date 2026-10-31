"""Injection Sentinel: flags instruction-like content in untrusted text. Deterministic.

Findings are surfaced; flagged text is still treated purely as data and can never change policy.
"""

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|above|prior|all|any)\b.{0,30}\b(instructions?|rules?|polic(y|ies)|prompts?)\b",
            re.I,
        ),
    ),
    ("role_hijack", re.compile(r"\b(you are now|act as|pretend to be|new system prompt)\b", re.I)),
    (
        "destructive_jira_action",
        re.compile(
            r"\b(delete|drop|remove|wipe)\b.{0,30}\b(project|epic|issue|ticket|board|jira)\b", re.I
        ),
    ),
    (
        "policy_bypass",
        re.compile(
            r"\b(bypass|skip|disable|override)\b.{0,25}\b(policy|approval|review|safeguards?|checks?)\b",
            re.I,
        ),
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(reveal|print|show|send|email)\b.{0,30}\b(system prompt|api[_ ]?key|token|password|secret|credentials?)\b",
            re.I,
        ),
    ),
    ("fake_delimiter", re.compile(r"</?\s*(system|assistant|transcript|instructions?)\s*>", re.I)),
]


@dataclass(frozen=True)
class Finding:
    rule: str
    segment_index: int
    excerpt: str


def scan(segment_texts: list[str]) -> list[Finding]:
    out: list[Finding] = []
    for i, text in enumerate(segment_texts):
        for rule, pat in _PATTERNS:
            m = pat.search(text)
            if m:
                out.append(Finding(rule, i, text[max(0, m.start() - 20) : m.end() + 20][:160]))
    return out
