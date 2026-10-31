import asyncio
import logging
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.orchestration import jobs
from app.persistence.db import unit_of_work
from app.persistence.models import Job

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        factory: sessionmaker[Session],
        handlers: dict[str, jobs.Handler],
        worker_id: str | None = None,
        client_id: uuid.UUID | None = None,
    ) -> None:
        self.client_id = client_id
        self.factory = factory
        self.handlers = handlers
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"

    def recover(self, stale_seconds: float = 600.0) -> int:
        from datetime import timedelta

        with unit_of_work(self.factory) as s:
            return jobs.recover_stale(s, timedelta(seconds=stale_seconds))

    async def run_once(self) -> bool:
        """Claim and process one job. Returns False when the queue is empty."""
        # Claim is committed (status=running) before the handler runs, so a crash
        # mid-handler leaves a visible 'running' job that recover() resumes.
        with unit_of_work(self.factory) as s:
            job = jobs.claim(s, self.worker_id, list(self.handlers), self.client_id)
            if job is None:
                return False
            job_id, kind = job.id, job.kind
        error: str | None = None
        with unit_of_work(self.factory) as s:
            job = s.get(Job, job_id)
            assert job is not None
            try:
                await self.handlers[kind](job)
            except Exception as exc:  # handler failure is data, not a crash
                log.warning("job %s failed: %s", job_id, exc)
                error = f"{type(exc).__name__}: {exc}"
            if error is None:
                jobs.mark_succeeded(s, job)
            else:
                jobs.mark_failed_attempt(s, job, error)
        return True

    async def run_until_empty(self, max_jobs: int = 1000) -> int:
        n = 0
        while n < max_jobs and await self.run_once():
            n += 1
        return n

    async def run_forever(
        self, poll_s: float = 0.25, tick: Callable[[Session], None] | None = None
    ) -> None:
        self.recover()
        while True:
            if tick is not None:
                with unit_of_work(self.factory) as s:
                    tick(s)
            if not await self.run_once():
                await asyncio.sleep(poll_s)
