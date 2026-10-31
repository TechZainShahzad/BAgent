import asyncio
import threading
import uuid
from collections import Counter

from sqlalchemy import select

from app.orchestration import jobs
from app.orchestration.audit import verify_client_chain
from app.orchestration.runner import JobRunner
from app.persistence.models import Client, Job


def test_four_competing_workers_run_every_job_exactly_once(factory):
    kind = f"soak-{uuid.uuid4().hex[:8]}"
    with factory() as s:
        clients = [Client(name=f"soak-{uuid.uuid4().hex[:8]}") for _ in range(3)]
        s.add_all(clients)
        s.flush()
        ids = [c.id for c in clients]
        for i in range(150):
            jobs.enqueue(s, ids[i % 3], kind, f"e{i}")
        s.commit()

    seen: Counter[str] = Counter()
    lock = threading.Lock()

    async def handler(job: Job) -> None:
        with lock:
            seen[f"{job.client_id}:{job.event_id}"] += 1
        await asyncio.sleep(0)

    def work() -> None:
        runner = JobRunner(factory, {kind: handler})
        asyncio.run(runner.run_until_empty())

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert len(seen) == 150 and set(seen.values()) == {1}  # no loss, no double execution
    with factory() as s:
        rows = list(s.scalars(select(Job).where(Job.kind == kind)))
        assert len(rows) == 150 and {j.status for j in rows} == {"succeeded"}
        for cid in ids:  # concurrent audit appends kept every chain valid
            assert verify_client_chain(s, cid) is None
