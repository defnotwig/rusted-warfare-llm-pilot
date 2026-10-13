"""Unit tests for 3-gram Jaccard, RapidFuzz, and TF-IDF cosine originality checks."""

from __future__ import annotations

from localshorts.services.originality import (
    compute_pair_similarity,
    ngram_jaccard_similarity,
    tfidf_cosine_similarity,
)
from localshorts.services.scripting import build_deterministic_fallback_script


def test_identical_texts_have_similarity_above_threshold() -> None:
    text = (
        "Most AI tools send every word you type straight to a remote cloud server. "
        "Local AI flips that architecture completely by running compact four-billion-parameter models "
        "directly on your laptop GPU and system memory."
    )
    assert ngram_jaccard_similarity(text, text, n=3) == 1.0
    assert tfidf_cosine_similarity(text, text) >= 0.99


def test_distinct_stories_pass_078_originality_gate() -> None:
    cand = build_deterministic_fallback_script("Why local AI runs on your laptop", is_smoke_test=True)
    prior_script = (
        "Sidemen rented an entire stadium in London to film their largest charity football match ever. "
        "Over ninety thousand fans filled the seats while millions more watched the live stream on YouTube. "
        "By the final whistle, the event raised millions for charity and set a new creator sports record."
    )
    metrics = compute_pair_similarity(
        candidate=cand,
        prior_script=prior_script,
        prior_hook="Sidemen rented an entire stadium in London.",
        prior_titles=["How Sidemen Sold Out a 90,000 Seat Stadium"],
        prior_summary="Sidemen charity football match in London.",
    )
    assert metrics["composite"] < 0.78
