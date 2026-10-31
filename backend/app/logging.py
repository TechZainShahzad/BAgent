"""JSON logging with secret redaction. Never log prompts, tokens or secrets."""

import json
import logging
import re
import sys
from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_PATTERNS = [
    re.compile(r"ATATT[A-Za-z0-9_\-=]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)(\"?\s*[:=]\s*\"?)([^\s\",}]+)"),
    re.compile(r"(?i)(authorization:\s*(basic|bearer)\s+)\S+"),
]


def redact(text: str) -> str:
    text = _PATTERNS[0].sub("[REDACTED]", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return _PATTERNS[2].sub(lambda m: f"{m.group(1)}[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id.get(),
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload["ctx"] = json.loads(redact(json.dumps(extra, default=str)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
