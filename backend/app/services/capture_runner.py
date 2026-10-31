"""Start, watch and stop the live-capture program from the dashboard.

The capture program needs this machine's audio devices, so the API process launches it as a
child process (argument list, no shell). The child reuses the dashboard user's session instead of
a password, writes its output to a log file we can tail, and ends gracefully when a stop file
appears (it then closes the meeting and queues the analysis exactly like Ctrl-C does).
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

STOP_GRACE_S = 25.0
LOG_TAIL_LINES = 120


class CaptureParams(BaseModel):
    client_id: uuid.UUID
    project_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    consent_basis: str = Field(min_length=3, max_length=1000)
    mode: Literal["interview", "copilot"] = "interview"
    autopilot: bool = False
    remote_name: str = Field(default="", max_length=120)
    remote_role: str = Field(default="", max_length=120)
    remote_authority: bool = False
    use_loopback: bool = True
    loopback_device: str = Field(default="", max_length=200)
    use_mic: bool = False
    mic_device: str = Field(default="", max_length=200)
    agent_output_device: str = Field(default="", max_length=200)
    agent_voice: str = Field(default="", max_length=120)
    duration_min: int = Field(default=0, ge=0, le=480)
    model: Literal["tiny.en", "base.en", "small.en", "medium.en"] = "small.en"

    @field_validator(
        "title",
        "consent_basis",
        "remote_name",
        "remote_role",
        "loopback_device",
        "mic_device",
        "agent_output_device",
        "agent_voice",
    )
    @classmethod
    def _no_option_injection(cls, v: str) -> str:
        v = v.strip()
        if v.startswith("-"):
            raise ValueError("value must not start with '-'")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "CaptureParams":
        if not (self.use_loopback or self.use_mic):
            raise ValueError("choose at least one audio source (system audio or microphone)")
        if self.autopilot and self.mode != "interview":
            raise ValueError("autopilot needs interview mode")
        if self.autopilot and not (self.remote_name and self.remote_authority):
            raise ValueError(
                "autopilot needs the remote speaker's name and 'can confirm requirements'; "
                "otherwise nothing can be confirmed and nothing would be created"
            )
        return self


def cli_executable() -> list[str]:
    """The same interpreter that runs the API, invoking the project's CLI module."""
    return [sys.executable, "-m", "app.cli.main"]


def build_command(params: CaptureParams, api_url: str, stop_file: Path) -> list[str]:
    p = params
    cmd = [
        *cli_executable(),
        "capture",
        str(p.client_id),
        p.title,
        "--consent-basis",
        p.consent_basis,
        "--api",
        api_url,
        "--model",
        p.model,
        "--stop-file",
        str(stop_file),
        "--mic" if p.use_mic else "--no-mic",
        "--loopback" if p.use_loopback else "--no-loopback",
    ]
    if p.mode == "interview":
        cmd.append("--interview")
    if p.autopilot:
        cmd.append("--autopilot")
    if p.project_id:
        cmd += ["--project-id", str(p.project_id)]
    if p.remote_name:
        cmd += ["--remote-name", p.remote_name]
    if p.remote_role:
        cmd += ["--remote-role", p.remote_role]
    if p.remote_authority:
        cmd.append("--remote-authority")
    if p.loopback_device:
        cmd += ["--loopback-device", p.loopback_device]
    if p.mic_device:
        cmd += ["--mic-device", p.mic_device]
    if p.agent_output_device:
        cmd += ["--agent-output-device", p.agent_output_device]
    if p.agent_voice:
        cmd += ["--agent-voice", p.agent_voice]
    if p.duration_min:
        cmd += ["--duration", str(p.duration_min * 60)]
    return cmd


class CaptureBusyError(Exception):
    """A capture is already running (there is one set of audio devices)."""


@dataclass
class _Run:
    run_id: str
    params: CaptureParams
    proc: Any
    log_path: Path
    stop_file: Path
    started_at: datetime
    started_by: str
    stop_requested_at: float | None = None
    meeting_id: str | None = None
    ended_at: datetime | None = None
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)


Launcher = Callable[[Sequence[str], dict[str, str], Path], Any]


def _default_launcher(cmd: Sequence[str], env: dict[str, str], log_path: Path) -> Any:
    log = open(log_path, "ab", buffering=0)  # noqa: SIM115 - handed to the child for its lifetime
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(  # noqa: S603 - argument list, no shell
        list(cmd),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=flags,
        cwd=str(Path(__file__).resolve().parents[2]),
    )


class CaptureManager:
    def __init__(
        self,
        launcher: Launcher = _default_launcher,
        runs_dir: Path | None = None,
        command_builder: Callable[[CaptureParams, str, Path], list[str]] = build_command,
    ) -> None:
        self._launcher = launcher
        self._dir = runs_dir or Path(tempfile.gettempdir()) / "ba-capture-runs"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._build = command_builder
        self._run: _Run | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control
    def start(
        self,
        params: CaptureParams,
        api_url: str,
        session_cookie: str,
        csrf: str,
        started_by: str,
    ) -> str:
        with self._lock:
            self._refresh()
            if self._run is not None and self._run.exit_code is None:
                raise CaptureBusyError("a call capture is already running; end it first")
            run_id = uuid.uuid4().hex[:10]
            log_path = self._dir / f"capture-{run_id}.log"
            stop_file = self._dir / f"capture-{run_id}.stop"
            stop_file.unlink(missing_ok=True)
            cmd = self._build(params, api_url, stop_file)
            env = {
                **os.environ,
                "BA_SESSION_COOKIE": session_cookie,
                "BA_CSRF": csrf,
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            env.pop("ANTHROPIC_API_KEY", None)
            proc = self._launcher(cmd, env, log_path)
            self._run = _Run(
                run_id=run_id,
                params=params,
                proc=proc,
                log_path=log_path,
                stop_file=stop_file,
                started_at=datetime.now(UTC),
                started_by=started_by,
                command=list(cmd),
            )
            return run_id

    def stop(self) -> bool:
        """Ask the capture to end the call gracefully. Returns False if nothing is running."""
        with self._lock:
            self._refresh()
            r = self._run
            if r is None or r.exit_code is not None:
                return False
            if r.stop_requested_at is None:
                r.stop_requested_at = time.monotonic()
                r.stop_file.write_text("stop", encoding="utf-8")
            return True

    # ------------------------------------------------------------------ status
    def _refresh(self) -> None:
        r = self._run
        if r is None:
            return
        if r.exit_code is None:
            code = r.proc.poll()
            if code is not None:
                r.exit_code, r.ended_at = int(code), datetime.now(UTC)
            elif r.stop_requested_at and time.monotonic() - r.stop_requested_at > STOP_GRACE_S:
                r.proc.terminate()  # did not end in time: hard stop (meeting stays recoverable)
        if r.meeting_id is None:
            for line in self._read_lines(r, 400):
                if line.startswith("MEETING_ID "):
                    r.meeting_id = line.split(" ", 1)[1].strip()

    @staticmethod
    def _read_lines(r: _Run, n: int) -> list[str]:
        try:
            data = r.log_path.read_bytes()[-60_000:]
        except OSError:
            return []
        return data.decode("utf-8", errors="replace").replace("\r\n", "\n").splitlines()[-n:]

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            r = self._run
            if r is None:
                return {"state": "idle"}
            running = r.exit_code is None
            state = (
                "stopping" if running and r.stop_requested_at else "running" if running else "ended"
            )
            return {
                "state": state,
                "run_id": r.run_id,
                "client_id": str(r.params.client_id),
                "project_id": str(r.params.project_id) if r.params.project_id else None,
                "title": r.params.title,
                "mode": r.params.mode,
                "autopilot": r.params.autopilot,
                "started_at": r.started_at.isoformat(),
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "started_by": r.started_by,
                "meeting_id": r.meeting_id,
                "exit_code": r.exit_code,
                "log": self._read_lines(r, LOG_TAIL_LINES),
            }
