from typing import Protocol

from pydantic import BaseModel


class SpeechSegment(BaseModel):
    start_s: float
    end_s: float
    text: str
    channel: str = "unknown"  # mic | loopback | unknown
    speaker: str | None = None
    speaker_confidence: float | None = None  # None = unknown, never invented


class SpeechProvider(Protocol):
    name: str

    def transcribe(
        self, pcm16_mono: bytes, sample_rate: int, channel: str
    ) -> list[SpeechSegment]: ...


class FakeSpeechProvider:
    name = "fake"

    def __init__(self, canned: list[SpeechSegment] | None = None) -> None:
        self.canned = canned or []

    def transcribe(self, pcm16_mono: bytes, sample_rate: int, channel: str) -> list[SpeechSegment]:
        return [s.model_copy(update={"channel": channel}) for s in self.canned]
