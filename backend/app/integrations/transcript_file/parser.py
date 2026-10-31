"""Transcript file adapter (R0): .vtt / .txt / .docx -> TranscriptSegment list.

Never invents data: missing speaker stays None, missing timestamps stay None.
"""

import re
from pathlib import Path

from pydantic import BaseModel

MAX_BYTES = 5 * 1024 * 1024


class TranscriptParseError(Exception):
    """Visible, typed parse failure (empty, too large, wrong type, undecodable)."""


class ParsedSegment(BaseModel):
    index: int
    start_s: float | None = None
    end_s: float | None = None
    speaker: str | None = None  # None = unknown; never guessed
    text: str


_TS = r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_CUE = re.compile(rf"^\s*{_TS}\s*-->\s*{_TS}")
_VOICE = re.compile(r"^<v\s+([^>]+)>(.*)$")
_TAG = re.compile(r"</?[^>]+>")
_SPEAKER_LINE = re.compile(
    r"^\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?([A-Za-z][\w .'\-]{0,60}?):\s+(.+)$"
)


def _to_s(h: str | None, m: str, s: str, ms: str | None) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + (int((ms or "0").ljust(3, "0")) / 1000)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise TranscriptParseError("transcript is not valid UTF-8/UTF-16 text")


def parse_vtt(text: str) -> list[ParsedSegment]:
    lines = text.replace("\r\n", "\n").split("\n")
    segs: list[ParsedSegment] = []
    i = 0
    while i < len(lines):
        m = _CUE.match(lines[i])
        if not m:
            i += 1
            continue
        g = m.groups()
        start, end = _to_s(*g[0:4]), _to_s(*g[4:8])
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i].strip())
            i += 1
        speaker, parts = None, []
        for ln in body:
            v = _VOICE.match(ln)
            if v:
                speaker = speaker or v.group(1).strip()
                ln = v.group(2)
            parts.append(_TAG.sub("", ln).strip())
        txt = " ".join(p for p in parts if p)
        if txt:
            segs.append(
                ParsedSegment(index=len(segs), start_s=start, end_s=end, speaker=speaker, text=txt)
            )
    return segs


def parse_txt(text: str) -> list[ParsedSegment]:
    segs: list[ParsedSegment] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            continue
        m = _SPEAKER_LINE.match(raw)
        if m:
            ts, speaker, body = m.groups()
            start = None
            if ts:
                parts = [int(x) for x in ts.split(":")]
                start = float(
                    parts[0] * 60 + parts[1]
                    if len(parts) == 2
                    else parts[0] * 3600 + parts[1] * 60 + parts[2]
                )
            segs.append(
                ParsedSegment(
                    index=len(segs), start_s=start, speaker=speaker.strip(), text=body.strip()
                )
            )
        else:
            segs.append(ParsedSegment(index=len(segs), text=raw.strip()))
    return segs


def parse_docx(path: Path) -> list[ParsedSegment]:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise TranscriptParseError("python-docx is required for .docx transcripts") from exc
    try:
        doc = Document(str(path))
    except Exception as exc:
        raise TranscriptParseError(f"cannot read .docx: {exc}") from exc
    return parse_txt("\n".join(p.text for p in doc.paragraphs))


def parse_transcript_file(path: Path) -> list[ParsedSegment]:
    if not path.exists():
        raise TranscriptParseError(f"file not found: {path}")
    if path.stat().st_size == 0:
        raise TranscriptParseError("transcript file is empty")
    if path.stat().st_size > MAX_BYTES:
        raise TranscriptParseError(f"transcript exceeds {MAX_BYTES} bytes")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        segs = parse_docx(path)
    elif suffix in {".vtt", ".txt"}:
        text = _decode(path.read_bytes())
        segs = (
            parse_vtt(text)
            if suffix == ".vtt" or text.lstrip().startswith("WEBVTT")
            else parse_txt(text)
        )
    else:
        raise TranscriptParseError(f"unsupported transcript type {suffix!r}")
    if not segs:
        raise TranscriptParseError("no transcript segments found")
    return segs
