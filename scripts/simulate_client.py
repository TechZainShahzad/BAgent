"""Rehearsal helper: plays a NON-TECHNICAL client by voice so the interviewer can be tested alone.

A second Claude call writes the client's spoken replies; Windows TTS (a different voice from the
agent) speaks them through the speakers; the capture companion hears them via loopback.

Usage (from backend/):  python -m uv run python ../scripts/simulate_client.py <client_id> <meeting_id>
Not part of the product: it exists only to rehearse the product without a second human.
"""

import asyncio
import os
import sys
import time
import uuid

from pydantic import BaseModel
from sqlalchemy import select

from app.capture.voice import VoiceOutput
from app.persistence.db import unit_of_work
from app.persistence.models import Meeting, TranscriptSegment
from app.providers.llm.base import LLMRequest, generate_validated, schema_for
from app.providers.llm.claude_cli import ClaudeCliProvider

PERSONA = """You are Mrs. Okafor, the head teacher of Greenfield Primary School. You are NOT technical
and you are speaking on a video call with a business analyst about a new school portal.
Facts you know (reveal them gradually, only when asked or naturally relevant):
- 450 pupils in grades 1 to 6, 25 teachers, 3 office staff. About 400 families.
- Today: paper report cards and a WhatsApp group; fees are tracked in an old Excel sheet.
- Parents complain they never know their child's marks, attendance or what fees are due.
- You want parents to log in and see grades and attendance, see and pay fees online, and read
  school announcements. Teachers should enter grades and attendance. You, the head, want
  simple reports (attendance, fees outstanding).
- Parents mostly use phones. Some are not comfortable with computers.
- You worry about children's information: only a child's own parents (and their teachers) may see it.
- Parents should get a text message when fees are due. Fees are paid via a local mobile-money service.
- It should be ready before the new term in about four months. Budget is modest.
- Success: fewer phone calls to the office, most families using it within a term.
Speaking style: natural spoken English, 1 to 3 short sentences, a little vague at first, no
technical words. If asked for numbers, give rough ones from the facts. If the analyst restates
something correctly, say "Yes, that's right." (you may add a short extra). If it is wrong, correct it
briefly. If thanked/closed, say goodbye politely. Never invent technology choices."""


class Reply(BaseModel):
    reply: str


async def main(client_id: str, meeting_id: str) -> None:
    llm = ClaudeCliProvider(timeout_s=120)
    voice = VoiceOutput(voice="Microsoft David Desktop", rate=0)
    answered_idx = -1
    started = time.monotonic()
    first = True
    while time.monotonic() - started < 900:
        with unit_of_work() as s:
            m = s.get(Meeting, uuid.UUID(meeting_id))
            if m is None or m.status not in {"live", "degraded"}:
                print("[client] meeting is no longer live; stopping", flush=True)
                return
            rows = list(
                s.scalars(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.meeting_id == m.id)
                    .order_by(TranscriptSegment.idx)
                )
            )
        lines = [(r.idx, r.channel == "agent", r.text) for r in rows]
        if lines and lines[-1][1] and lines[-1][0] > answered_idx:
            idx = lines[-1][0]
            convo = "\n".join(f"{'ANALYST' if a else 'YOU'}: {t}" for _, a, t in lines[-14:])
            req = LLMRequest(
                agent="sim_client",
                system_prompt=PERSONA,
                user_prompt=f"Conversation so far:\n{convo}\n\nWrite ONLY your next spoken reply.",
                output_schema=schema_for(Reply),
                client_id=client_id,
            )
            out, _ = await generate_validated(llm, req, Reply)
            if first and os.environ.get("SIM_FIRST"):
                out = Reply(reply=os.environ["SIM_FIRST"])  # scripted opener for follow-up rehearsals
            answered_idx = idx
            print(f"[client] {out.reply}", flush=True)
            time.sleep(0.8 if first else 0.4)
            first = False
            voice.speak(out.reply)
        else:
            time.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
