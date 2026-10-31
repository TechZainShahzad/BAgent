"""Eval & Replay Harness: scores extraction and question quality on the labeled dataset.

Matching uses embedding similarity between expected statements and extracted ones, so wording
differences do not count as misses. Thresholds for gating come from a MEASURED baseline
(baseline.json), never from guesses.
"""

import json
from pathlib import Path
from typing import Any

from app.agents.requirements_analyst import SegmentIn, analyze
from app.integrations.transcript_file.parser import ParsedSegment, parse_transcript_file, parse_txt
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider

HERE = Path(__file__).parent
MATCH_SIM = 0.78  # bge-small-en-v1.5: paraphrase ~0.85-0.95, related-but-different ~0.7
QUESTION_SIM = 0.65
FORBIDDEN_SIM = 0.75


def load_dataset() -> list[dict[str, Any]]:
    return json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))


def segments_for(item: dict[str, Any]) -> list[ParsedSegment]:
    if "file" in item:
        return parse_transcript_file(HERE / item["file"])
    return parse_txt("\n".join(item["lines"]))


def _cos(a: list[float], b: list[float]) -> float:
    import math

    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def _best(embedder: EmbeddingProvider, needles: list[str], hay: list[str]) -> list[float]:
    if not needles:
        return []
    if not hay:
        return [0.0] * len(needles)
    vecs = embedder.embed(needles + hay)
    n = len(needles)
    return [max(_cos(vecs[i], vecs[n + j]) for j in range(len(hay))) for i in range(n)]


async def evaluate_item(
    provider: LLMProvider, embedder: EmbeddingProvider, item: dict[str, Any]
) -> dict[str, Any]:
    parsed = segments_for(item)
    segs = [
        SegmentIn(index=s.index, speaker=s.speaker, text=s.text, start_s=s.start_s) for s in parsed
    ]
    result = await analyze(provider, "eval", segs)
    extracted = [f"{g.requirement.title}. {g.requirement.statement}" for g in result.accepted]
    questions = [q.question for q in result.questions]
    expected, forbidden, must_ask = item["expected"], item["forbidden"], item["must_ask"]

    recall_hits = (
        sum(s >= MATCH_SIM for s in _best(embedder, expected, extracted)) if expected else 0
    )
    matched = sum(s >= MATCH_SIM for s in _best(embedder, extracted, expected)) if extracted else 0
    leaks = (
        sum(s >= FORBIDDEN_SIM for s in _best(embedder, extracted, forbidden)) if forbidden else 0
    )
    ask_hits = (
        sum(s >= QUESTION_SIM for s in _best(embedder, must_ask, questions)) if must_ask else 0
    )
    return {
        "id": item["id"],
        "expected": len(expected),
        "extracted": len(extracted),
        "recall_hits": recall_hits,
        "precision_hits": matched,
        "ungrounded_rejected": len(result.rejected),
        "forbidden_leaks": leaks,
        "must_ask": len(must_ask),
        "must_ask_hits": ask_hits,
        "injection_flagged": len(result.injection_findings),
        "unmatched_extracted": [
            e
            for e, s in zip(extracted, _best(embedder, extracted, expected), strict=True)
            if s < MATCH_SIM
        ],
        "missed_expected": [
            e
            for e, s in zip(expected, _best(embedder, expected, extracted), strict=True)
            if s < MATCH_SIM
        ],
    }


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    exp = sum(i["expected"] for i in items)
    ext = sum(i["extracted"] for i in items)
    ask = sum(i["must_ask"] for i in items)
    return {
        "items": len(items),
        "recall": round(sum(i["recall_hits"] for i in items) / exp, 3) if exp else None,
        "precision": round(sum(i["precision_hits"] for i in items) / ext, 3) if ext else None,
        "question_coverage": round(sum(i["must_ask_hits"] for i in items) / ask, 3)
        if ask
        else None,
        "forbidden_leaks": sum(i["forbidden_leaks"] for i in items),
        "ungrounded_rejected": sum(i["ungrounded_rejected"] for i in items),
        "expected_total": exp,
        "extracted_total": ext,
    }


async def run(provider: LLMProvider, embedder: EmbeddingProvider) -> dict[str, Any]:
    items = [await evaluate_item(provider, embedder, it) for it in load_dataset()]
    return {"aggregate": aggregate(items), "items": items}
