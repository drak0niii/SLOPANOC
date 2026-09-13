"""Phase 6A.5: deterministic fusion (`backend/knowledge/hybrid_retrieval/fusion.py`)."""
from __future__ import annotations

from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, RetrievalChannel
from backend.knowledge.hybrid_retrieval.fusion import fuse


def _hit(evidence_id: str, channel: RetrievalChannel, score: float) -> ChannelHit:
    return ChannelHit(evidence_id=evidence_id, channel=channel, raw_score=score)


def test_single_channel_hit_gets_a_score() -> None:
    scores = fuse({"exact": [_hit("e1", RetrievalChannel.EXACT, 1.0)]})
    assert scores["e1"] > 0.0


def test_multi_channel_hit_scores_higher_than_single_channel() -> None:
    single = fuse({"exact": [_hit("e1", RetrievalChannel.EXACT, 1.0)]})
    multi = fuse(
        {
            "exact": [_hit("e1", RetrievalChannel.EXACT, 1.0)],
            "lexical": [_hit("e1", RetrievalChannel.LEXICAL, 0.5)],
        }
    )
    assert multi["e1"] > single["e1"]


def test_higher_raw_score_within_a_channel_ranks_better() -> None:
    scores = fuse(
        {
            "lexical": [
                _hit("high", RetrievalChannel.LEXICAL, 0.9),
                _hit("low", RetrievalChannel.LEXICAL, 0.1),
            ]
        }
    )
    assert scores["high"] > scores["low"]


def test_tied_raw_scores_within_a_channel_receive_equal_contribution() -> None:
    scores = fuse(
        {
            "lexical": [
                _hit("a", RetrievalChannel.LEXICAL, 0.5),
                _hit("b", RetrievalChannel.LEXICAL, 0.5),
            ]
        }
    )
    assert scores["a"] == scores["b"]


def test_order_independent_with_respect_to_channel_dict_iteration_order() -> None:
    channel_a = [_hit("e1", RetrievalChannel.EXACT, 1.0)]
    channel_b = [_hit("e1", RetrievalChannel.LEXICAL, 0.5), _hit("e2", RetrievalChannel.LEXICAL, 0.9)]
    forward = fuse({"exact": channel_a, "lexical": channel_b})
    backward = fuse({"lexical": channel_b, "exact": channel_a})
    assert forward == backward


def test_empty_input_produces_empty_scores() -> None:
    assert fuse({}) == {}
    assert fuse({"exact": []}) == {}


def test_no_model_call_source_proof() -> None:
    """Structural proof, not a claim: no google.adk/google.genai import
    anywhere in this module."""
    import ast
    import inspect

    from backend.knowledge.hybrid_retrieval import fusion as module

    tree = ast.parse(inspect.getsource(module))
    forbidden = {"google.adk", "google.genai"}
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not (imported & forbidden)
