from app.domain.audit import GENESIS, compute_hash, verify_chain


def _chain(n):
    prev, out = GENESIS, []
    for i in range(n):
        payload = {"i": i}
        h = compute_hash(prev, "evt", payload)
        out.append(("evt", prev, payload, h))
        prev = h
    return out


def test_valid_chain():
    assert verify_chain(_chain(5)) is None


def test_tampered_payload_detected():
    ch = _chain(5)
    ch[2] = ("evt", ch[2][1], {"i": 999}, ch[2][3])
    assert verify_chain(ch) == 2


def test_removed_event_detected():
    ch = _chain(5)
    del ch[1]
    assert verify_chain(ch) == 1


def test_reordered_detected():
    ch = _chain(4)
    ch[1], ch[2] = ch[2], ch[1]
    assert verify_chain(ch) is not None
