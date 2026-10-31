"""Warm Claude CLI sessions for the live interviewer.

Spawning `claude -p` costs about 4 s per call before the model even starts. A long-lived process
fed over stream-json answers in about 1.3 s. Isolation rules (CLAUDE.md principle 6):
- one process per `warm_key` (the caller scopes it to client AND meeting), never shared;
- the system prompt and schema are fixed per process; a different pair gets a different process;
- every turn still carries its full context in the prompt, so the process memory is never relied on;
- the process is recycled after MAX_TURNS turns, when idle, on any error, and on close().
Any warm failure raises; the caller falls back to a cold one-shot call. Nothing is hidden.
"""

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
from typing import Any

from app.providers.llm.base import (
    LLMTimeoutError,
    NonZeroExitError,
    SchemaMismatchError,
)

log = logging.getLogger(__name__)

MAX_TURNS = 8
IDLE_S = 120.0
STREAM_LIMIT = 16 * 1024 * 1024


class WarmProcess:
    def __init__(self, args: list[str], model_name: str) -> None:
        self.args = args
        self.model_name = model_name
        self.turns = 0
        self.last_used = time.monotonic()
        self._proc: asyncio.subprocess.Process | None = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: list[str] = []
        self.lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        self._tmp = tempfile.TemporaryDirectory(prefix="ba-llm-warm-")
        self._proc = await asyncio.create_subprocess_exec(
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._tmp.name,
            env=env,
            limit=STREAM_LIMIT,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while line := await self._proc.stderr.readline():
                self._stderr_tail = (self._stderr_tail + [line.decode(errors="replace")])[-20:]
        except Exception:  # draining is best effort; errors surface through the main path
            return

    async def ask(self, prompt: str, timeout_s: float) -> dict[str, Any]:
        """Send one user turn, return the result envelope. Raises typed errors."""
        assert self._proc is not None and self._proc.stdin is not None
        assert self._proc.stdout is not None
        msg = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
        }
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(self._read_result(), timeout=timeout_s)
        except TimeoutError as exc:
            raise LLMTimeoutError(f"warm claude exceeded {timeout_s}s") from exc
        finally:
            self.turns += 1
            self.last_used = time.monotonic()

    async def _read_result(self) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                tail = "".join(self._stderr_tail)[-300:]
                raise NonZeroExitError(f"warm claude exited ({self._proc.returncode}): {tail}")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-protocol noise
            if isinstance(event, dict) and event.get("type") == "result":
                return event

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        if self._tmp is not None:
            with contextlib.suppress(OSError):  # Windows may hold the empty temp dir briefly
                self._tmp.cleanup()


def check_envelope(event: dict[str, Any], classify: Any) -> dict[str, Any]:
    if event.get("is_error"):
        raise classify(1, "", str(event.get("result", "")))
    data = event.get("structured_output")
    if not isinstance(data, dict):
        raise SchemaMismatchError("warm claude returned no structured_output object")
    return data


class WarmPool:
    def __init__(self) -> None:
        self._procs: dict[str, WarmProcess] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            async with self._lock:
                await self._reap_locked()

    async def acquire(self, key: str, args: list[str], model: str) -> WarmProcess:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_loop())
        async with self._lock:
            await self._reap_locked()
            wp = self._procs.get(key)
            if wp is not None and (not wp.alive or wp.turns >= MAX_TURNS):
                await wp.close()
                wp = None
            if wp is None:
                wp = WarmProcess(args, model)
                await wp.start()
                self._procs[key] = wp
            return wp

    async def drop(self, key: str) -> None:
        async with self._lock:
            wp = self._procs.pop(key, None)
        if wp is not None:
            await wp.close()

    async def _reap_locked(self) -> None:
        now = time.monotonic()
        for k in [k for k, w in self._procs.items() if now - w.last_used > IDLE_S or not w.alive]:
            await self._procs.pop(k).close()

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for k in list(self._procs):
            await self.drop(k)
