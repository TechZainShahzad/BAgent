"""Energy-based utterance segmenter. Pure numpy; audio stays in memory (never written to disk)."""

from collections import deque
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class Utterance:
    samples: np.ndarray  # float32 mono, 16 kHz
    start_s: float
    end_s: float


class UtteranceSegmenter:
    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        min_speech_s: float = 0.4,
        end_silence_s: float = 0.7,
        max_utterance_s: float = 20.0,
        pre_roll_s: float = 0.3,
        abs_threshold: float = 0.01,
        noise_multiple: float = 3.0,
    ) -> None:
        self.sr = sample_rate
        self.min_speech = min_speech_s
        self.end_silence = end_silence_s
        self.max_len = max_utterance_s
        self.abs_threshold = abs_threshold
        self.noise_multiple = noise_multiple
        self._pre: deque[np.ndarray] = deque()
        self._pre_samples = int(pre_roll_s * sample_rate)
        self._buf: list[np.ndarray] = []
        self._speaking = False
        self._silence_run = 0.0
        self._speech_s = 0.0
        self._t = 0.0  # seconds of audio consumed
        self._utt_start = 0.0
        self._noise = abs_threshold / noise_multiple

    def _is_speech(self, rms: float) -> bool:
        return rms > max(self.abs_threshold, self._noise * self.noise_multiple)

    def feed(self, frame: np.ndarray) -> list[Utterance]:
        """Feed a mono float32 frame; returns zero or more completed utterances."""
        frame = np.asarray(frame, dtype=np.float32).reshape(-1)
        dur = len(frame) / self.sr
        rms = float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0
        out: list[Utterance] = []
        speech = self._is_speech(rms)
        if not speech:  # slowly track the noise floor from non-speech frames only
            self._noise = 0.95 * self._noise + 0.05 * rms
        if not self._speaking:
            if speech:
                self._speaking = True
                self._silence_run = 0.0
                pre = list(self._pre)
                pre_len = sum(len(p) for p in pre)
                self._utt_start = max(0.0, self._t - pre_len / self.sr)
                self._buf = [*pre, frame]
                self._speech_s = dur
            else:
                self._pre.append(frame)
                while sum(len(p) for p in self._pre) > self._pre_samples and len(self._pre) > 1:
                    self._pre.popleft()
        else:
            self._buf.append(frame)
            self._silence_run = 0.0 if speech else self._silence_run + dur
            if speech:
                self._speech_s += dur
            length = sum(len(b) for b in self._buf) / self.sr
            if self._silence_run >= self.end_silence or length >= self.max_len:
                out.extend(self._close())
        self._t += dur
        return out

    def flush(self) -> list[Utterance]:
        return self._close() if self._speaking else []

    def _close(self) -> list[Utterance]:
        samples = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        speech_len = self._speech_s
        start = self._utt_start
        self._buf, self._speaking, self._silence_run, self._speech_s = [], False, 0.0, 0.0
        self._pre.clear()
        if speech_len < self.min_speech:
            return []
        return [Utterance(samples, start, start + len(samples) / self.sr)]
