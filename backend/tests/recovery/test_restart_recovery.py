import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.orchestration import jobs
from app.orchestration.runner import JobRunner
from app.persistence.models import Job


async def test_crash_mid_job_is_resumed_after_restart(factory, client_id):
    kind = f"k-{uuid.uuid4().hex[:8]}"
    with factory() as s:
        job_id, _ = jobs.enqueue(s, client_id, kind, "e1")
        s.commit()
    # Worker 1 claims (commits status=running) and then "dies" before finishing.
    with factory() as s:
        assert jobs.claim(s, "dead-worker", [kind]) is not None
        s.commit()
    with factory() as s:
        s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(locked_at=datetime.now(UTC) - timedelta(hours=1))
        )
        s.commit()

    ran = []

    async def handler(job: Job):
        ran.append(job.id)

    runner = JobRunner(factory, {kind: handler})
    assert runner.recover(stale_seconds=60) >= 1  # startup resume
    assert await runner.run_until_empty() == 1
    assert ran == [job_id]
    with factory() as s:
        assert s.get(Job, job_id).status == "succeeded"


async def test_fresh_running_job_is_not_stolen(factory, client_id):
    kind = f"k-{uuid.uuid4().hex[:8]}"

    async def noop(job: Job):
        return None

    with factory() as s:
        job_id, _ = jobs.enqueue(s, client_id, kind, "e1")
        jobs.claim(s, "alive-worker", [kind])
        s.commit()
    JobRunner(factory, {kind: noop}).recover(stale_seconds=600)
    with factory() as s:
        assert s.get(Job, job_id).status == "running"
