import json
import logging

from app.logging import JsonFormatter, redact
from app.services.auth import hash_password, verify_password

TOKEN = "ATATT3x" + "FAKE0FAKE0FAKE0FAKE0FAKE0FAKE0FAKE0FAKE0FAKE0" + "=00000000"


def test_redacts_atlassian_token():
    assert TOKEN not in redact(f"using {TOKEN} now")


def test_redacts_key_value_and_auth_header():
    assert "hunter2" not in redact("password=hunter2 and api_key: hunter2")
    assert "abc123" not in redact("Authorization: Basic abc123")


def test_formatter_redacts_ctx():
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "msg %s", (TOKEN,), None)
    rec.ctx = {"token": "supersecret"}
    out = json.loads(JsonFormatter().format(rec))
    assert TOKEN not in json.dumps(out)
    assert "supersecret" not in json.dumps(out)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_set_password_validates_and_changes_the_hash(factory):
    import uuid

    import pytest

    from app.services.auth import authenticate, create_user, set_password

    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with factory() as s:
        create_user(s, email, "the-first-password-1")
        s.commit()
        assert authenticate(s, email, "the-first-password-1") is not None
        with pytest.raises(ValueError):
            set_password(s, email, "short")
        with pytest.raises(LookupError):
            set_password(s, "nobody@example.com", "a-valid-long-password")
        set_password(s, email, "the-second-password-2")
        s.commit()
        assert authenticate(s, email, "the-first-password-1") is None
        assert authenticate(s, email, "the-second-password-2") is not None
