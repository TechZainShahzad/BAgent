"""Measures how long the agent takes to have its reply ready after the client stops talking.

Drives a REAL interview meeting through the running API + worker + Claude (no audio): it plays the
capture app's role (posts what the "client" said, polls next-utterance exactly like the companion),
and prints the wait for each turn. Audio-side delays (end-of-speech detection, transcription,
speech start) are separate and are listed in the summary from measured constants.

Usage (from backend/, API and worker running):
  .\\.venv\\Scripts\\python.exe ..\\scripts\\measure_reply_latency.py <client_id> <email> <password>
The meeting is ended at the end; it is a normal (clearly titled) rehearsal meeting.
"""

import statistics
import sys
import time

from app.capture.client import CaptureClient

ANSWERS = [
    "We want parents to see their child's grades on their phone.",
    "Only the child's own parents, and the teachers, should see it.",
    "About four hundred families, and twenty five teachers enter the marks.",
    "The head teacher approves the report cards before parents can see them.",
    "Parents should get a text message when fees are due.",
]


def main(client_id: str, email: str, password: str) -> None:
    cc = CaptureClient()
    cc.login(email, password)
    mid = cc.start(
        client_id,
        "Latency measurement (rehearsal)",
        "Practice call, no real participants",
        {"mode": "interview", "autopilot": False, "remote_name": "Rehearsal", "remote_authority": True},
    )
    waits: list[float] = []
    clock = 0.0
    last_hb = 0.0
    try:
        for i, answer in enumerate([None, *ANSWERS]):
            if answer is not None:
                clock += 6.0
                t_sent = time.monotonic()
                cc.segments(
                    client_id,
                    mid,
                    [{"text": answer, "start_s": clock, "end_s": clock + 4.0, "channel": "loopback", "speaker": None}],
                )
            else:
                t_sent = time.monotonic()
            out: dict = {}
            while time.monotonic() - t_sent < 90:
                if time.monotonic() - last_hb > 5:
                    cc.heartbeat(client_id, mid)  # a live capture sends these every 5 s
                    last_hb = time.monotonic()
                out = cc.next_utterance(client_id, mid)
                if out.get("speak"):
                    break
                time.sleep(0.2)  # same cadence as the companion
            took = time.monotonic() - t_sent
            if not out.get("speak"):
                print(f"turn {i}: NO REPLY within 90 s ({out.get('reason')})")
                continue
            cc.agent_said(client_id, mid, out["speak"], out.get("action", "ask"), out.get("topic", "other"))
            if answer is None:
                for _ in range(4):  # the real greeting takes about 8 s to speak
                    time.sleep(2)
                    cc.heartbeat(client_id, mid)
            tag = "opening" if answer is None else "reply  "
            if answer is not None:
                waits.append(took)
            print(f"turn {i} {tag} {took:5.1f}s  {out['speak'][:80]}")
    finally:
        cc.end(client_id, mid)
    if waits:
        print(
            f"\nserver side, from 'segment received' to 'reply released': "
            f"median {statistics.median(waits):.1f}s, max {max(waits):.1f}s over {len(waits)} turns"
        )
        print("add roughly: end-of-speech 0.7 s + transcription ~1 s + speech start <0.3 s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
