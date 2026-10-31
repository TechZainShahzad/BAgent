"""Local speech-to-text via faster-whisper, behind the SpeechProvider port. English only.

Free and local: the model is downloaded once from the public hub; audio never leaves the machine.
Uses CUDA when it actually works, otherwise falls back to CPU int8 (and says so).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.providers.speech.base import SpeechSegment

log = logging.getLogger(__name__)

# Whisper is known to hallucinate on near-silence; these are dropped and COUNTED, not hidden.
MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0


@dataclass
class SttStats:
    accepted: int = 0
    dropped_low_confidence: int = 0
    device: str = "unloaded"
    notes: list[str] = field(default_factory=list)


class FasterWhisperProvider:
    name = "faster_whisper"

    def __init__(self, model_size: str = "small.en", prefer_cuda: bool = True) -> None:
        self.model_size = model_size
        self.prefer_cuda = prefer_cuda
        self.stats = SttStats()
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel

        if self.prefer_cuda:
            try:
                model = WhisperModel(self.model_size, device="cuda", compute_type="float16")
                # force a tiny decode so missing CUDA libraries fail HERE, not mid-meeting
                list(model.transcribe(np.zeros(16000, np.float32), language="en")[0])
                self.stats.device = "cuda"
                self._model = model
                return model
            except Exception as exc:
                self.stats.notes.append(f"CUDA unavailable ({type(exc).__name__}); using CPU int8")
                log.warning("CUDA unavailable for STT: %s", exc)
        self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        self.stats.device = "cpu"
        return self._model

    def transcribe_array(self, samples: np.ndarray, channel: str) -> list[SpeechSegment]:
        model = self._load()
        segments, _ = model.transcribe(
            samples,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        out: list[SpeechSegment] = []
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            if s.no_speech_prob > MAX_NO_SPEECH_PROB or s.avg_logprob < MIN_AVG_LOGPROB:
                self.stats.dropped_low_confidence += 1
                continue
            self.stats.accepted += 1
            out.append(
                SpeechSegment(
                    start_s=float(s.start),
                    end_s=float(s.end),
                    text=text,
                    channel=channel,
                    speaker=None,  # never invent identity
                    speaker_confidence=None,
                )
            )
        return out

    def transcribe(self, pcm16_mono: bytes, sample_rate: int, channel: str) -> list[SpeechSegment]:
        samples = np.frombuffer(pcm16_mono, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            raise ValueError("faster-whisper provider expects 16 kHz audio")
        return self.transcribe_array(samples, channel)
