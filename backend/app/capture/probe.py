"""Audio probes run in a short-lived process (Windows audio needs its own COM-initialised thread,
which the API's worker threads do not have). Prints exactly one JSON object.

  python -m app.capture.probe options
  python -m app.capture.probe test-voice <device> <voice>
  python -m app.capture.probe test-audio <device>
"""

import json
import sys


def _options() -> dict[str, object]:
    out: dict[str, object] = {
        "available": False,
        "reason": "",
        "microphones": [],
        "loopback": [],
        "speakers": [],
        "voices": [],
        "default_speaker": None,
        "cable_found": False,
    }
    try:
        from app.capture import audio

        devices = audio.list_devices()
        out["microphones"], out["loopback"] = devices["microphones"], devices["loopback"]
        speakers = audio.list_speakers()
        out["speakers"] = speakers
        out["default_speaker"] = audio.default_speaker_name()
        out["cable_found"] = any("CABLE Input" in n for n in speakers)
        out["available"] = True
    except Exception as exc:
        out["reason"] = f"{type(exc).__name__}: {exc}"
    try:
        from app.capture.voice import list_voices

        out["voices"] = list_voices()
    except Exception as exc:
        out["reason"] = (str(out["reason"]) + f" | voices: {exc}").strip(" |")
    return out


def main(argv: list[str]) -> dict[str, object]:
    cmd = argv[0] if argv else "options"
    if cmd == "options":
        return _options()
    if cmd == "test-voice":
        from app.capture.voice import VoiceOutput

        device, voice = (argv + ["", ""])[1:3]
        VoiceOutput(device or None, voice or None).speak(
            "Testing, one, two, three. This is the business analyst assistant."
        )
        return {"ok": True}
    if cmd == "test-audio":
        from app.capture import audio

        peak = audio.measure_loopback((argv + [""])[1] or None, 2.0)
        return {"peak": round(peak, 3), "hearing_sound": peak > 0.01}
    raise SystemExit(f"unknown probe {cmd}")


if __name__ == "__main__":
    try:
        print(json.dumps(main(sys.argv[1:])))
    except Exception as exc:  # report, never hang or crash the caller
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
