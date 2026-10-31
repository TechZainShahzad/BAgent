import json
from pathlib import Path

import pytest

from app.providers.llm.fake import FakeLLMProvider
from tests.eval import harness

BASELINE = Path(__file__).parent / "baseline.json"


class BagEmbedder:
    """Test embedder: hashed bag-of-words, so repeated/reworded identical content still matches."""

    name = "bag"
    dimensions = 384

    def embed(self, texts):
        import re
        import zlib

        out = []
        for t in texts:
            v = [0.0] * 384
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[zlib.crc32(w.encode()) % 384] += 1.0
            out.append(v)
        return out


# Thresholds derive from the MEASURED baseline minus a tolerance for model variance.
TOLERANCE = 0.1


def test_dataset_is_well_formed():
    data = harness.load_dataset()
    assert len(data) >= 6 and len({d["id"] for d in data}) == len(data)
    for d in data:
        assert {"expected", "forbidden", "must_ask"} <= d.keys()
        assert harness.segments_for(d)  # parses


async def test_harness_scores_a_perfect_and_an_empty_model():
    emb = BagEmbedder()
    item = harness.load_dataset()[2]  # wishlist
    segs = harness.segments_for(item)

    def perfect(req):
        reqs = []
        for i, exp in enumerate(item["expected"]):
            seg = segs[i]
            reqs.append(
                {
                    "title": exp,
                    "statement": exp,
                    "kind": "functional",
                    "explicit": True,
                    "actors": [],
                    "segment_index": seg.index,
                    "quote": seg.text[:40],
                }
            )
        return {"requirements": reqs, "open_questions": []}

    res = await harness.evaluate_item(FakeLLMProvider(perfect), emb, item)
    assert res["recall_hits"] == res["expected"] == 4 and res["precision_hits"] == 4
    empty = await harness.evaluate_item(FakeLLMProvider(lambda r: {}), emb, item)
    assert empty["recall_hits"] == 0 and empty["extracted"] == 0


async def test_fabricated_quotes_count_as_ungrounded_not_as_hits():
    emb = BagEmbedder()
    item = harness.load_dataset()[2]

    def fabricated(req):
        return {
            "requirements": [
                {
                    "title": "Gift cards",
                    "statement": "Gift cards at launch",
                    "kind": "functional",
                    "explicit": True,
                    "actors": [],
                    "segment_index": 0,
                    "quote": "we will definitely ship gift cards",
                }
            ]
        }

    res = await harness.evaluate_item(FakeLLMProvider(fabricated), emb, item)
    assert res["extracted"] == 0 and res["ungrounded_rejected"] == 1


def test_aggregate_math():
    agg = harness.aggregate(
        [
            {
                "expected": 4,
                "extracted": 5,
                "recall_hits": 4,
                "precision_hits": 4,
                "must_ask": 2,
                "must_ask_hits": 1,
                "forbidden_leaks": 0,
                "ungrounded_rejected": 1,
            },
            {
                "expected": 0,
                "extracted": 0,
                "recall_hits": 0,
                "precision_hits": 0,
                "must_ask": 0,
                "must_ask_hits": 0,
                "forbidden_leaks": 1,
                "ungrounded_rejected": 0,
            },
        ]
    )
    assert agg["recall"] == 1.0 and agg["precision"] == 0.8 and agg["question_coverage"] == 0.5
    assert agg["forbidden_leaks"] == 1


def test_baseline_file_is_committed_and_sane():
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert base["aggregate"]["forbidden_leaks"] == 0
    assert base["aggregate"]["recall"] >= 0.8


@pytest.mark.real_llm
@pytest.mark.real_embed
async def test_real_model_has_not_regressed_against_baseline():
    from app.providers.embeddings.fastembed_provider import FastEmbedProvider
    from app.providers.llm.claude_cli import ClaudeCliProvider

    base = json.loads(BASELINE.read_text(encoding="utf-8"))["aggregate"]
    now = (await harness.run(ClaudeCliProvider(timeout_s=240), FastEmbedProvider()))["aggregate"]
    assert now["forbidden_leaks"] == 0  # injection must never leak: hard gate
    assert (
        now["ungrounded_rejected"] == 0
        or now["ungrounded_rejected"] <= base["ungrounded_rejected"] + 1
    )
    for key in ("recall", "precision", "question_coverage"):
        assert now[key] >= base[key] - TOLERANCE, (key, now[key], base[key])
