"""The agent's voice: offline Windows TTS (System.Speech), played to a chosen output device.

- Output device: the default speakers, or e.g. a virtual audio cable ("CABLE Input") so the voice
  appears in Teams as the laptop microphone.
- Generated speech is rendered to a temporary WAV that is deleted immediately (it is the agent's
  own words, never captured meeting audio).
"""

import contextlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np


class VoiceError(Exception):
    pass


_SYNTH_PS = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "if ($env:BA_TTS_VOICE) { $s.SelectVoice($env:BA_TTS_VOICE) };"
    "$s.Rate = [int]$env:BA_TTS_RATE;"
    "$s.SetOutputToWaveFile($env:BA_TTS_OUT);"
    "$s.Speak($env:BA_TTS_TEXT);"
    "$s.Dispose()"
)
# A long-lived synthesizer: one request per stdin line (JSON), one "DONE"/"ERR ..." line back.
# Starting PowerShell and loading System.Speech for every sentence costs about a second.
_SERVER_PS = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "if ($env:BA_TTS_VOICE) { $s.SelectVoice($env:BA_TTS_VOICE) };"
    "[Console]::Out.WriteLine('READY'); [Console]::Out.Flush();"
    "while (($l = [Console]::In.ReadLine()) -ne $null) {"
    " try { $j = $l | ConvertFrom-Json; $s.Rate = [int]$j.rate;"
    " $s.SetOutputToWaveFile($j.out); $s.Speak($j.text); $s.SetOutputToNull();"
    " [Console]::Out.WriteLine('DONE') }"
    " catch { [Console]::Out.WriteLine('ERR ' + $_.Exception.Message) };"
    " [Console]::Out.Flush() }"
)
_VOICES_PS = (
    "Add-Type -AssemblyName System.Speech;"
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
    "ForEach-Object { $_.VoiceInfo.Name }"
)


def list_voices() -> list[str]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _VOICES_PS],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [v.strip() for v in out.stdout.splitlines() if v.strip()]


def synthesize(text: str, voice: str | None = None, rate: int = 0) -> tuple[np.ndarray, int]:
    """Render text to float32 mono samples. Raises VoiceError on any failure (never silent)."""
    with tempfile.TemporaryDirectory(prefix="ba-tts-") as tmp:
        wav = Path(tmp) / "say.wav"
        env = {
            **os.environ,
            "BA_TTS_TEXT": text,
            "BA_TTS_OUT": str(wav),
            "BA_TTS_VOICE": voice or "",
            "BA_TTS_RATE": str(rate),
        }
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _SYNTH_PS],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0 or not wav.exists():
            raise VoiceError(f"TTS failed: {r.stderr.strip()[:200]}")
        return _read_wav(wav)


class TtsServer:
    """Persistent PowerShell synthesizer. Any failure raises VoiceError; callers fall back to the
    one-shot synthesize() so the agent still speaks (slower) instead of going silent."""

    def __init__(self, voice: str | None) -> None:
        self._lock = threading.Lock()
        env = {**os.environ, "BA_TTS_VOICE": voice or ""}
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", _SERVER_PS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        try:
            first = self._lines.get(timeout=30)
        except queue.Empty as exc:
            self.close()
            raise VoiceError("TTS server did not start in time") from exc
        if first != "READY":
            self.close()
            raise VoiceError(f"TTS server did not start: {first[:100]}")

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line.strip())
        self._lines.put("EOF")

    def synthesize(self, text: str, rate: int) -> tuple[np.ndarray, int]:
        assert self._proc.stdin is not None
        with self._lock, tempfile.TemporaryDirectory(prefix="ba-tts-") as tmp:
            wav = Path(tmp) / "say.wav"
            req = json.dumps({"text": text, "rate": rate, "out": str(wav)})  # ASCII-escaped
            try:
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
                reply = self._lines.get(timeout=60)
            except (OSError, queue.Empty) as exc:
                raise VoiceError(f"TTS server unavailable: {exc}") from exc
            if reply != "DONE" or not wav.exists():
                raise VoiceError(f"TTS server failed: {reply[:200]}")
            return _read_wav(wav)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._proc.kill()


def _read_wav(wav: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(wav), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise VoiceError(f"unexpected TTS sample width {width}")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


class VoiceOutput:
    def __init__(self, device: str | None = None, voice: str | None = None, rate: int = 0) -> None:
        self.device = device
        self.voice = voice
        self.rate = rate
        self.speaking = threading.Event()
        self._server: TtsServer | None = None
        self._server_failed = False

    def _synth(self, text: str) -> tuple[np.ndarray, int]:
        if not self._server_failed:
            try:
                if self._server is None:
                    self._server = TtsServer(self.voice)
                return self._server.synthesize(text, self.rate)
            except VoiceError:
                self._server_failed = True  # use the slower one-shot path from now on
                if self._server is not None:
                    self._server.close()
                    self._server = None
        return synthesize(text, self.voice, self.rate)

    def close(self) -> None:
        if self._server is not None:
            self._server.close()

    def _speaker(self) -> Any:
        import soundcard as sc

        if not self.device:
            return sc.default_speaker()
        for s in sc.all_speakers():
            if self.device.lower() in s.name.lower():
                return s
        raise VoiceError(f"output device {self.device!r} not found")

    def speak(self, text: str, cancel: threading.Event | None = None) -> bool:
        """Speak and block. Returns False if cancelled (barge-in) before finishing."""
        samples, sr = self._synth(text)
        spk = self._speaker()
        chunk = int(sr * 0.2)
        self.speaking.set()
        try:
            with spk.player(samplerate=sr, channels=1, blocksize=chunk) as player:
                for i in range(0, len(samples), chunk):
                    if cancel is not None and cancel.is_set():
                        return False
                    player.play(samples[i : i + chunk])
                player.play(np.zeros(int(sr * 0.25), np.float32))  # flush tail
        finally:
            self.speaking.clear()
        return True

    def duration_hint(self, text: str) -> float:
        return max(1.0, len(text.split()) / 2.6)  # rough words/second, for logging only


def close_quietly(obj: Any) -> None:
    with contextlib.suppress(Exception):
        obj.close()
