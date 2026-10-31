"""Claude Code CLI adapter (headless). Flags verified in docs/spikes/claude-cli.md.

Safety: argument-list subprocess (no shell), prompt via stdin, no built-in tools, no session
persistence, empty temp working dir, ANTHROPIC_API_KEY stripped so the subscription login is used.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from app.providers.llm.base import (
    CancelledByCallerError,
    CliMissingError,
    InvalidJsonError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    ModelUnavailableError,
    NonZeroExitError,
    NotAuthenticatedError,
    SchemaMismatchError,
    UsageLimitError,
)
from app.providers.llm.warm import WarmPool, check_envelope

log = logging.getLogger(__name__)

_AUTH_RE = re.compile(
    r"(not logged in|please run /login|invalid api key|authentication|unauthorized)", re.I
)
_LIMIT_RE = re.compile(r"(usage limit|rate limit|limit reached|quota|overloaded)", re.I)
_MODEL_RE = re.compile(
    r"(model.*(not found|unavailable|invalid)|invalid model|unknown model)", re.I
)


def resolve_claude_bin() -> str:
    """Return a directly spawnable claude executable. Windows npm shims are .cmd files that
    cannot be spawned without a shell, so find the real claude.exe the shim wraps."""
    found = shutil.which("claude")
    if not found:
        raise CliMissingError("claude CLI not found on PATH")
    path = Path(found)
    if path.suffix.lower() in {".cmd", ".bat", ""} and os.name == "nt":
        shim_dir = path.parent
        candidate = (
            shim_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        )
        if candidate.exists():
            return str(candidate)
        cmd = shim_dir / "claude.cmd"
        if cmd.exists():
            m = re.search(r'"%dp0%\\+([^"]+claude\.exe)"', cmd.read_text(errors="ignore"))
            if m and (shim_dir / m.group(1)).exists():
                return str(shim_dir / m.group(1))
        raise CliMissingError(f"found {found} but could not resolve the real claude.exe")
    return found


def classify_failure(returncode: int, stderr: str, envelope_text: str) -> Exception:
    blob = f"{stderr}\n{envelope_text}"
    if _AUTH_RE.search(blob):
        return NotAuthenticatedError("claude CLI is not authenticated")
    if _LIMIT_RE.search(blob):
        return UsageLimitError("claude CLI usage/rate limit reached")
    if _MODEL_RE.search(blob):
        return ModelUnavailableError("requested model unavailable")
    return NonZeroExitError(f"claude CLI exited with code {returncode}")


class ClaudeCliProvider:
    name = "claude_cli"

    def __init__(
        self,
        model: str = "sonnet",
        timeout_s: float = 300.0,
        max_concurrency: int = 1,
        retries: int = 1,
        log_content: bool = False,
        bin_path: str | None = None,
        warm: bool = True,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.retries = retries
        self.log_content = log_content
        self._bin = bin_path
        self._sem = asyncio.Semaphore(max_concurrency)
        self._warm_enabled = warm
        self._warm_sem = asyncio.Semaphore(2)  # live replies never queue behind batch analysis
        self._pool = WarmPool()

    def _args(self, request: LLMRequest, stream: bool = False) -> list[str]:
        fmt = (
            ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
            if stream  # warm session: a streaming conversation over stdin/stdout
            else ["--output-format", "json"]
        )
        return [
            self._bin or resolve_claude_bin(),
            "-p",
            "--model",
            self.model,
            *fmt,
            "--tools",
            "",
            "--no-session-persistence",
            "--json-schema",
            json.dumps(request.output_schema),
            "--system-prompt",
            request.system_prompt,
        ]

    async def close(self) -> None:
        await self._pool.close()

    async def _warm_once(self, request: LLMRequest) -> LLMResponse:
        assert request.warm_key
        key = f"{request.client_id}|{request.warm_key}|{request.input_hash_static()}"
        started = time.monotonic()
        wp = await self._pool.acquire(key, self._args(request, stream=True), self.model)
        try:
            async with wp.lock:
                event = await wp.ask(request.user_prompt, min(self.timeout_s, 90.0))
            data = check_envelope(event, classify_failure)
        except Exception:
            await self._pool.drop(key)  # never reuse a process that just failed
            raise
        usage = event.get("usage") or {}
        return LLMResponse(
            data=data,
            provider=self.name,
            model=next(iter(event.get("modelUsage") or {}), self.model),
            duration_ms=int((time.monotonic() - started) * 1000),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=event.get("total_cost_usd"),
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if self._warm_enabled and request.warm_key:
            try:
                async with self._warm_sem:
                    return await self._warm_once(request)
            except (
                LLMTimeoutError,
                NonZeroExitError,
                InvalidJsonError,
                SchemaMismatchError,
                OSError,
            ) as exc:
                log.warning(
                    "warm claude failed, falling back to a one-shot call",
                    extra={"ctx": {"class": type(exc).__name__, "agent": request.agent}},
                )
        return await self._generate_cold(request)

    async def _generate_cold(self, request: LLMRequest) -> LLMResponse:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._sem:
                    return await self._once(request)
            except (LLMTimeoutError, NonZeroExitError, InvalidJsonError) as exc:
                last = exc  # transient classes: bounded retry with backoff
                if attempt < self.retries:
                    await asyncio.sleep(2.0 * (attempt + 1))
        assert last is not None
        raise last

    async def _once(self, request: LLMRequest) -> LLMResponse:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ba-llm-") as cwd:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._args(request),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise CliMissingError("claude CLI not found") from exc
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(request.user_prompt.encode()), timeout=self.timeout_s
                )
            except TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise LLMTimeoutError(f"claude CLI exceeded {self.timeout_s}s") from exc
            except asyncio.CancelledError as exc:
                proc.kill()
                await proc.wait()
                raise CancelledByCallerError("LLM call cancelled") from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        out_text = stdout.decode(errors="replace")
        err_text = stderr.decode(errors="replace")
        envelope: dict[str, Any] | None = None
        try:
            parsed = json.loads(out_text)
            envelope = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            envelope = None

        if proc.returncode != 0 or (envelope and envelope.get("is_error")):
            result_text = str(envelope.get("result", "")) if envelope else ""
            err = classify_failure(proc.returncode or 1, err_text, result_text)
            log.warning(
                "claude cli failure",
                extra={
                    "ctx": {
                        "exit": proc.returncode,
                        "class": type(err).__name__,
                        "duration_ms": duration_ms,
                        "agent": request.agent,
                    }
                },
            )
            raise err
        if envelope is None:
            raise InvalidJsonError("claude CLI stdout was not a JSON object")
        data = envelope.get("structured_output")
        if not isinstance(data, dict):
            raise SchemaMismatchError("claude CLI returned no structured_output object")
        usage = envelope.get("usage") or {}
        model_usage = envelope.get("modelUsage") or {}
        log.info(
            "claude cli ok",
            extra={
                "ctx": {
                    "duration_ms": duration_ms,
                    "agent": request.agent,
                    "terminal_reason": envelope.get("terminal_reason"),
                }
            },
        )
        return LLMResponse(
            data=data,
            provider=self.name,
            model=next(iter(model_usage), self.model),
            duration_ms=duration_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=envelope.get("total_cost_usd"),
        )
