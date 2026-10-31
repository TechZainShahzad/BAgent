"""Warm interviewer sessions: reuse, isolation, recycling, fallback. Uses a fake CLI script."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from app.providers.llm import claude_cli, warm
from app.providers.llm.base import LLMRequest, UsageLimitError

FAKE = textwrap.dedent(
    """
    import json, os, sys
    mode = sys.argv[1]
    def result(prompt):
        if prompt == "LIMIT":
            return {"type": "result", "is_error": True, "result": "usage limit reached"}
        return {"type": "result", "is_error": False,
                "structured_output": {"pid": os.getpid(), "mode": mode, "echo": prompt}}
    if mode == "oneshot":
        print(json.dumps(result(sys.stdin.read())))
    else:
        for line in sys.stdin:
            prompt = json.loads(line)["message"]["content"][0]["text"]
            if prompt == "DIE":
                sys.exit(3)
            print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
            print(json.dumps(result(prompt)), flush=True)
    """
)


class Provider(claude_cli.ClaudeCliProvider):
    script: str = ""

    def _args(self, request, stream=False):  # type: ignore[no-untyped-def]
        return [sys.executable, self.script, "stream" if stream else "oneshot"]


@pytest.fixture
async def provider(tmp_path: Path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE)
    p = Provider(retries=0)
    p.script = str(script)
    yield p
    await p.close()


def req(prompt: str, client: str = "c1", key: str | None = "m1") -> LLMRequest:
    return LLMRequest(
        agent="interviewer",
        system_prompt="s",
        user_prompt=prompt,
        output_schema={"type": "object"},
        client_id=client,
        warm_key=key,
    )


async def test_same_meeting_reuses_one_warm_process(provider):
    a = await provider.generate(req("one"))
    b = await provider.generate(req("two"))
    assert a.data["mode"] == "stream" and a.data["pid"] == b.data["pid"]
    assert b.data["echo"] == "two"  # each turn still carries its own full prompt


async def test_different_meeting_or_client_never_shares_a_process(provider):
    a = await provider.generate(req("x", client="c1", key="m1"))
    b = await provider.generate(req("x", client="c1", key="m2"))
    c = await provider.generate(req("x", client="c2", key="m1"))
    assert len({a.data["pid"], b.data["pid"], c.data["pid"]}) == 3


async def test_no_warm_key_uses_a_one_shot_call(provider):
    r = await provider.generate(req("x", key=None))
    assert r.data["mode"] == "oneshot"


async def test_process_is_recycled_after_max_turns(provider):
    pids = [(await provider.generate(req(str(i)))).data["pid"] for i in range(warm.MAX_TURNS + 1)]
    assert len(set(pids[: warm.MAX_TURNS])) == 1 and pids[-1] != pids[0]


async def test_dead_warm_process_falls_back_to_one_shot_then_recovers(provider):
    first = await provider.generate(req("hello"))
    died = await provider.generate(
        req("DIE")
    )  # warm process exits: visible fallback, not a failure
    assert died.data["mode"] == "oneshot"
    again = await provider.generate(req("back"))
    assert again.data["mode"] == "stream" and again.data["pid"] != first.data["pid"]


async def test_usage_limit_is_raised_not_hidden_by_fallback(provider):
    with pytest.raises(UsageLimitError):
        await provider.generate(req("LIMIT"))


def test_warm_key_does_not_change_the_audit_input_hash():
    assert req("p", key="a").input_hash() == req("p", key="b").input_hash()
    assert json.loads(req("p").model_dump_json())["warm_key"] == "m1"
