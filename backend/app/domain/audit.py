"""Pure hash-chain helpers for tamper-evident audit events."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

GENESIS = "0" * 64


def canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, event_type: str, payload: Mapping[str, Any]) -> str:
    data = f"{prev_hash}|{event_type}|{canonical(payload)}".encode()
    return hashlib.sha256(data).hexdigest()


def verify_chain(events: Iterable[tuple[str, str, Mapping[str, Any], str]]) -> int | None:
    """events: (event_type, prev_hash, payload, hash) -> index of first bad event, or None."""
    expected_prev = GENESIS
    for i, (event_type, prev_hash, payload, stored) in enumerate(events):
        if prev_hash != expected_prev or compute_hash(prev_hash, event_type, payload) != stored:
            return i
        expected_prev = stored
    return None
