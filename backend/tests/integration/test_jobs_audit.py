import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.orchestration import jobs
from app.orchestration.audit import append_audit, verify_client_chain
from app.orchestration.runner import JobRunner
from app.persistence.models import Client, Job


def _kind():
    return f"k-{uuid.uuid4().hex[:8]}"


def test_duplicate_event_is_ignored(factory, client_id):
    kind = _kind()
    with factory() as s:
        _, created1 = jobs.enqueue(s, client_id, kind, "evt-1")
        _, created2 = jobs.enqueue(s, client_id, kind, "evt-1")
        s.commit()
    assert created1 and not created2


def test_same_event_id_allowed_for_other_client(factory, client_id):
    kind = _kind()
    with factory() as s:
        other = Client(name=f"c-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        _, a = jobs.enqueue(s, client_id, kind, "evt")
        _, b = jobs.enqueue(s, other.id, kind, "evt")
        s.commit()
    assert a and b


async def test_success_path(factory, client_id):
    kind = _kind()
    done = []

    async def handler(job: Job):
        done.append(job.event_id)

    with factory() as s:
        jobs.enqueue(s, client_id, kind, "e1")
        s.commit()
    assert await JobRunner(factory, {kind: handler}).run_until_empty() == 1
    assert done == ["e1"]
    with factory() as s:
        assert s.query(Job).filter_by(kind=kind).one().status == "succeeded"
        assert verify_client_chain(s, client_id) is None


async def test_poison_job_fails_with_reason_after_bounded_retries(factory, client_id, monkeypatch):
    kind = _kind()
    monkeypatch.setattr(jobs, "BACKOFF_BASE_S", 0.0)  # no waiting in tests

    async def boom(job: Job):
        raise RuntimeError("provider down")

    with factory() as s:
        jobs.enqueue(s, client_id, kind, "e1", max_attempts=3)
        s.commit()
    runner = JobRunner(factory, {kind: boom})
    assert await runner.run_until_empty() == 3
    with factory() as s:
        j = s.query(Job).filter_by(kind=kind).one()
        assert j.status == "failed" and j.attempts == 3
        assert "provider down" in j.last_error
        assert verify_client_chain(s, client_id) is None


def test_skip_locked_two_workers_get_different_jobs(factory, client_id):
    kind = _kind()
    with factory() as s:
        jobs.enqueue(s, client_id, kind, "a")
        jobs.enqueue(s, client_id, kind, "b")
        s.commit()
    s1, s2, s3 = factory(), factory(), factory()
    try:
        j1 = jobs.claim(s1, "w1", [kind])
        j2 = jobs.claim(s2, "w2", [kind])  # j1 row is locked, uncommitted
        assert j1 is not None and j2 is not None and j1.id != j2.id
        assert jobs.claim(s3, "w3", [kind]) is None
    finally:
        for x in (s1, s2, s3):
            x.rollback()
            x.close()


def test_empty_kind_list_claims_nothing(factory, client_id):
    with factory() as s:
        jobs.enqueue(s, client_id, _kind(), "x")
        s.commit()
    with factory() as s:
        assert jobs.claim(s, "w", []) is None


def test_audit_is_append_only(factory, client_id):
    with factory() as s:
        append_audit(s, "t.one", {"a": 1}, client_id)
        s.commit()
    with factory() as s, pytest.raises(DBAPIError, match="append-only"):
        s.execute(
            text("UPDATE audit_events SET payload = '{}' WHERE client_id = :c"), {"c": client_id}
        )
    with factory() as s, pytest.raises(DBAPIError, match="append-only"):
        s.execute(text("DELETE FROM audit_events WHERE client_id = :c"), {"c": client_id})


def test_audit_chain_valid_and_detects_forgery(factory, client_id):
    with factory() as s:
        for i in range(3):
            append_audit(s, "t.evt", {"i": i}, client_id)
        s.commit()
        assert verify_client_chain(s, client_id) is None
    # Simulate a privileged attacker disabling the trigger and editing a row.
    with factory() as s:
        s.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        s.execute(
            text(
                "UPDATE audit_events SET payload = CAST(:p AS jsonb) WHERE client_id = :c AND seq = 2"
            ),
            {"p": '{"i": 99}', "c": client_id},
        )
        s.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        s.commit()
    with factory() as s:
        assert verify_client_chain(s, client_id) == 1


def test_long_open_transaction_does_not_block_other_audit_writers(factory, client_id):
    """A job that holds its transaction open (e.g. awaiting an LLM) must not stall the live path."""
    import threading

    from app.orchestration.audit import count_events

    long_tx = factory()
    append_audit(long_tx, "analysis.step", {"n": 1}, client_id)  # buffered, no lock taken yet
    long_tx.execute(text("SELECT 1"))  # transaction is open and busy

    done = threading.Event()

    def live_request() -> None:
        with factory() as s:
            append_audit(s, "interview.agent_spoke", {"n": 2}, client_id)
            s.commit()
        done.set()

    t = threading.Thread(target=live_request)
    t.start()
    assert done.wait(5), "a concurrent audit write blocked behind a long-running transaction"
    t.join()
    append_audit(long_tx, "analysis.step", {"n": 3}, client_id)
    long_tx.commit()
    long_tx.close()
    with factory() as s:
        assert verify_client_chain(s, client_id) is None
        assert count_events(s, client_id) == 3


def test_rolled_back_transaction_leaves_no_audit_events(factory, client_id):
    from app.orchestration.audit import count_events

    with factory() as s:
        before = count_events(s, client_id)
        append_audit(s, "never.happened", {}, client_id)
        s.rollback()
        s.commit()
        assert count_events(s, client_id) == before
