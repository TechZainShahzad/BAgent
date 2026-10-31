"""Measure the baseline with the REAL Claude CLI and embeddings. Opt-in, costs LLM calls.

Run:  python -m uv run python -m tests.eval.run_baseline
Writes tests/eval/baseline.json (committed) and prints a summary.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.providers.embeddings.fastembed_provider import FastEmbedProvider
from app.providers.llm.claude_cli import ClaudeCliProvider
from tests.eval import harness


async def main() -> None:
    result = await harness.run(ClaudeCliProvider(timeout_s=240), FastEmbedProvider())
    result["measured_at"] = datetime.now(UTC).isoformat()
    result["model"] = "claude sonnet via CLI; embeddings bge-small-en-v1.5"
    result["match_similarity"] = harness.MATCH_SIM
    out = Path(__file__).parent / "baseline.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["aggregate"], indent=2))
    for i in result["items"]:
        print(
            i["id"],
            {
                k: i[k]
                for k in (
                    "expected",
                    "extracted",
                    "recall_hits",
                    "precision_hits",
                    "forbidden_leaks",
                )
            },
        )
        if i["missed_expected"]:
            print("   missed:", i["missed_expected"])
        if i["unmatched_extracted"]:
            print("   unmatched extracted:", i["unmatched_extracted"])


if __name__ == "__main__":
    asyncio.run(main())
