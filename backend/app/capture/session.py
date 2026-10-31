"""Capture session: sources -> utterance segmenter -> STT -> batched POST to the local API.

Transparency/safety behaviors:
- A heartbeat is sent continuously; if capture dies the backend marks the meeting degraded.
- API outages never drop transcript silently: segments are buffered and retried, and the outage
  is reported through on_status.
- Copilot mode: questions arrive via polling and are shown to the operator only.
- Interview mode: the agent speaks. Code on the server decides WHEN (pause, cooldown, freshness);
  this loop only plays what it is told and reports exactly what was said. While the agent speaks
  through the speakers the capture is muted (it must not hear itself); on a virtual cable it can
  be interrupted (barge-in) when the client starts talking.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import numpy as np

from app.capture.client import CaptureApiError, CaptureClient
from app.capture.vad import SAMPLE_RATE, Utterance, UtteranceSegmenter
from app.providers.speech.base import SpeechSegment

log = logging.getLogger(__name__)

BARGE_IN_RMS = 0.03
MUTE_TAIL_S = 0.8


class Transcriber(Protocol):
    def transcribe_array(self, samples: np.ndarray, channel: str) -> list[SpeechSegment]: ...


class Speaker(Protocol):
    speaking: threading.Event

    def speak(self, text: str, cancel: threading.Event | None = None) -> bool: ...


class CaptureSession:
    def __init__(
        self,
        client: CaptureClient,
        client_id: str,
        meeting_id: str,
        stt: Transcriber,
        on_suggestion: Callable[[dict[str, Any]], None] = lambda s: None,
        on_status: Callable[[str], None] = lambda m: None,
        post_interval_s: float = 2.0,
        heartbeat_interval_s: float = 5.0,
        suggestion_interval_s: float = 3.0,
        voice: Speaker | None = None,
        mute_during_speech: bool = True,
        utterance_poll_s: float = 0.2,
    ) -> None:
        self.client, self.client_id, self.meeting_id = client, client_id, meeting_id
        self.stt = stt
        self.on_suggestion, self.on_status = on_suggestion, on_status
        self.post_interval = post_interval_s
        self.hb_interval = heartbeat_interval_s
        self.sugg_interval = suggestion_interval_s
        self.voice = voice
        self.mute_during_speech = mute_during_speech
        self.utterance_poll_s = utterance_poll_s
        self._stop = threading.Event()
        self._utts: queue.Queue[tuple[str, Utterance] | None] = queue.Queue()
        self._pending: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()  # one delivery at a time keeps segment order
        self._threads: list[threading.Thread] = []
        self._stt_thread: threading.Thread = threading.Thread()
        self._sources: list[tuple[str, Iterable[np.ndarray]]] = []
        self._muted = threading.Event()
        self._mute_until = 0.0
        self._last_loud = 0.0
        self.end_requested = False
        self.sent = 0
        self.spoken = 0
        self.post_failures = 0
        self.errors: list[str] = []

    def add_source(self, channel: str, frames: Iterable[np.ndarray]) -> None:
        self._sources.append((channel, frames))

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------------------- workers
    def _is_muted(self) -> bool:
        return self._muted.is_set() or time.monotonic() < self._mute_until

    def _read(self, channel: str, frames: Iterable[np.ndarray]) -> None:
        seg = UtteranceSegmenter(SAMPLE_RATE)
        try:
            for frame in frames:
                if self._stop.is_set():
                    break
                if self._is_muted():
                    frame = np.zeros_like(frame)  # the agent's own voice must not be heard
                elif channel == "loopback":
                    rms = float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0
                    if rms > BARGE_IN_RMS:
                        self._last_loud = time.monotonic()
                for u in seg.feed(frame):
                    self._utts.put((channel, u))
        except Exception as exc:  # a dead audio device must be visible, not silent
            self.errors.append(f"{channel} source failed: {type(exc).__name__}: {exc}")
            self.on_status(f"AUDIO SOURCE FAILED ({channel}): {exc}")
        finally:
            for u in seg.flush():
                self._utts.put((channel, u))

    def _transcribe(self) -> None:
        while True:
            item = self._utts.get()
            if item is None:
                return
            channel, utt = item
            try:
                for s in self.stt.transcribe_array(utt.samples, channel):
                    with self._lock:
                        self._pending.append(
                            {
                                "text": s.text,
                                "start_s": utt.start_s + s.start_s,
                                "end_s": utt.start_s + s.end_s,
                                "channel": channel,
                                "speaker": None,
                            }
                        )
                if self.voice is not None:
                    self._flush_pending()  # interview mode: every 100 ms counts, send now
            except Exception as exc:
                self.errors.append(f"STT failed: {type(exc).__name__}: {exc}")
                self.on_status(f"STT FAILED: {exc}")

    def _flush_pending(self) -> None:
        with self._flush_lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        with self._lock:
            batch, self._pending = self._pending, []
        if not batch:
            return
        try:
            self.sent += self.client.segments(self.client_id, self.meeting_id, batch)
            if self.post_failures:
                self.on_status("API reachable again; buffered transcript delivered")
            self.post_failures = 0
        except CaptureApiError as exc:
            self.post_failures += 1
            with self._lock:  # keep, never drop
                self._pending = batch + self._pending
            self.on_status(f"API unreachable ({self.post_failures}x), buffering: {exc}")

    # ---------------------------------------------------------------- interview (agent speaks)
    def _speak(self, text: str) -> bool:
        assert self.voice is not None
        cancel = threading.Event()
        done = threading.Event()
        started = time.monotonic()

        def watch() -> None:  # barge-in: only meaningful when we are not muted (virtual cable)
            while not done.wait(0.05):
                if (
                    not self.mute_during_speech
                    and time.monotonic() - started > 1.0
                    and time.monotonic() - self._last_loud < 0.25
                ):
                    cancel.set()
                    return

        w = threading.Thread(target=watch, daemon=True)
        w.start()
        if self.mute_during_speech:
            self._muted.set()
        try:
            return self.voice.speak(text, cancel)
        finally:
            done.set()
            self._mute_until = time.monotonic() + MUTE_TAIL_S
            self._muted.clear()

    def _interview_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.utterance_poll_s)
            if self.voice is None or self.voice.speaking.is_set():
                continue
            try:
                out = self.client.next_utterance(self.client_id, self.meeting_id)
            except CaptureApiError as exc:
                self.on_status(f"utterance poll failed: {exc}")
                continue
            if out.get("end"):
                self.end_requested = True
                self._stop.set()
                return
            text = out.get("speak")
            if not text:
                continue
            try:
                finished = self._speak(text)
            except Exception as exc:  # TTS/device failure must be visible
                self.errors.append(f"voice failed: {type(exc).__name__}: {exc}")
                self.on_status(f"VOICE FAILED: {exc}")
                time.sleep(3)
                continue
            if not finished:
                self.on_status("interrupted by the client (barge-in); will re-plan")
                continue
            try:
                self.client.agent_said(
                    self.client_id,
                    self.meeting_id,
                    text,
                    out.get("action", "ask"),
                    out.get("topic", "other"),
                )
                self.spoken += 1
                self.on_status(f"AGENT: {text}")
            except CaptureApiError as exc:
                self.on_status(f"could not record what the agent said: {exc}")

    # ---------------------------------------------------------------- main loop
    def run(
        self,
        duration_s: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        for channel, frames in self._sources:
            t = threading.Thread(target=self._read, args=(channel, frames), daemon=True)
            t.start()
            self._threads.append(t)
        self._stt_thread = threading.Thread(target=self._transcribe, daemon=True)
        self._stt_thread.start()
        if self.voice is not None:
            threading.Thread(target=self._interview_loop, daemon=True).start()
        started = time.monotonic()
        last_post = last_hb = last_sug = started
        while not self._stop.is_set():
            now = time.monotonic()
            if duration_s is not None and now - started >= duration_s:
                break
            if should_stop is not None and should_stop():
                break  # asked to end (e.g. from the dashboard)
            if duration_s is None and not any(t.is_alive() for t in self._threads):
                break  # every source ended (a bounded run waits out its duration instead)
            if now - last_post >= self.post_interval:
                self._flush_pending()
                last_post = now
            if now - last_hb >= self.hb_interval:
                try:
                    self.client.heartbeat(self.client_id, self.meeting_id)
                except CaptureApiError as exc:
                    self.on_status(f"heartbeat failed: {exc}")
                last_hb = now
            if self.voice is None and now - last_sug >= self.sugg_interval:
                try:
                    sug = self.client.suggestion(self.client_id, self.meeting_id)
                    if sug.get("question"):
                        self.on_suggestion(sug["question"])
                except CaptureApiError as exc:
                    self.on_status(f"suggestion poll failed: {exc}")
                last_sug = now
            time.sleep(0.1)
        self._shutdown()

    def _shutdown(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._utts.put(None)
        self._stt_thread.join(timeout=120)  # finish transcribing what was already heard
        for _ in range(3):  # last delivery attempts so no transcript is lost
            self._flush_pending()
            with self._lock:
                if not self._pending:
                    break
            time.sleep(1)
