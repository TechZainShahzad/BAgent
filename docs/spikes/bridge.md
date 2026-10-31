# Spike D1 — local live capture (own tool, R2)

Status: PASSED on this machine, 2026-10-18. (No M365 tenant, so Teams-native routes were not tested; see `teams.md` deferral.)

## What was tested
Windows 11, `soundcard` 0.4.6 (WASAPI) loopback of the default output device + `faster-whisper` 1.2.1 `small.en`, English.
Test signal: Windows `System.Speech` TTS spoke two known sentences through the speakers while the loopback recorded.

| Check | Result |
|---|---|
| Loopback devices enumerated | 3 (Realtek speakers, NVIDIA HDMI monitor, NVIDIA Broadcast virtual) |
| Capture | 13.1 s captured, peak 0.97, rms 0.085 (clean signal) |
| Utterance segmentation | 2 utterances found at 2.1-6.6 s and 6.6-11.5 s |
| Transcription | Both sentences exact: "Users must reset their password by email within 5 minutes." / "The admin dashboard must show failed login attempts for the last 30 days." |
| Latency (CPU int8) | ~1.9 s to transcribe a ~5 s utterance after warm-up (about 0.4x realtime) |
| GPU | RTX 3050 present; CUDA path fails on this box (`cublas64_12.dll` not found). Provider detects this at load, logs it, falls back to CPU, and records the note in its stats. Installing the NVIDIA cuBLAS/cuDNN 12 runtime would enable it; not required. |
| Audio on disk | None (frames are in memory; a unit test asserts no files are created) |

## Limits (honest)
- Synthetic TTS is easier than overlapping human crosstalk; real-meeting WER is not measured. Evidence quotes match the STT text, so the operator must review evidence.
- No diarization: mic channel = operator, loopback = "remote participants". Speaker is stored as NULL; the Authority Resolver therefore never confirms from live speech alone.
- The default speaker here is a virtual device (NVIDIA Broadcast); capturing a meeting app that plays to a different device needs `--loopback-device`. Use `ba capture --list-devices`.
- First run downloads the model (~480 MB) from the public Hugging Face hub (no client data).

## Decision
R2 (`local_capture`) is viable and is the live route. R3/R4 stay deferred (ADR 0002). Participant (in-meeting chat) mode is not implemented; Copilot only (ADR 0004).
