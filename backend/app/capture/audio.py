"""Audio sources (Windows WASAPI via soundcard). Frames stay in memory; nothing is written to disk.

- Microphone: the operator's voice.
- Loopback: whatever the machine plays, i.e. the remote participants of Teams/Zoom/Meet/etc.
Neither identifies a person; the channel is all we know.
"""

import warnings
from collections.abc import Iterator
from typing import Any

import numpy as np

from app.capture.vad import SAMPLE_RATE

BLOCK = 480  # 30 ms at 16 kHz


def _sc() -> Any:
    import soundcard as sc

    warnings.filterwarnings("ignore", message="data discontinuity", module="soundcard")
    return sc


def list_devices() -> dict[str, list[str]]:
    sc = _sc()
    return {
        "microphones": [m.name for m in sc.all_microphones() if not m.isloopback],
        "loopback": [m.name for m in sc.all_microphones(include_loopback=True) if m.isloopback],
    }


def _frames(recorder_ctx: Any) -> Iterator[np.ndarray]:
    with recorder_ctx as rec:
        while True:
            data = rec.record(numframes=BLOCK)
            yield np.asarray(data, dtype=np.float32).reshape(len(data), -1).mean(axis=1)


def mic_frames(device: str | None = None) -> Iterator[np.ndarray]:
    sc = _sc()
    mic = sc.get_microphone(device) if device else sc.default_microphone()
    return _frames(mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK))


def loopback_frames(device: str | None = None) -> Iterator[np.ndarray]:
    sc = _sc()
    name = device or sc.default_speaker().name
    mic = sc.get_microphone(name, include_loopback=True)
    return _frames(mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK))


def list_speakers() -> list[str]:
    return [s.name for s in _sc().all_speakers()]


def default_speaker_name() -> str:
    return str(_sc().default_speaker().name)


def measure_loopback(device: str | None = None, seconds: float = 2.0) -> float:
    """Peak level (0..1) of whatever the machine is playing on `device` for a moment."""
    import time

    peak = 0.0
    end = time.monotonic() + seconds
    with loopback_recorder(device) as rec:
        while time.monotonic() < end:
            data = rec.record(numframes=BLOCK)
            peak = max(peak, float(np.abs(data).max()) if len(data) else 0.0)
    return peak


def loopback_recorder(device: str | None = None) -> Any:
    sc = _sc()
    name = device or sc.default_speaker().name
    return sc.get_microphone(name, include_loopback=True).recorder(
        samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK
    )
