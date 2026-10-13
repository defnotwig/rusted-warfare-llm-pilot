"""Originality and duplicate-detection engine using 3-gram Jaccard, RapidFuzz, and TF-IDF cosine similarity."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from rapidfuzz import fuzz
from sqlmodel import select

from localshorts.database import get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import ScriptRecord
from localshorts.models.schemas import ScriptGenerationSchema

logger = get_logger("originality")

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "is",
    "was",
    "are",
    "were",
    "be",
    "been",
    "this",
    "that",
    "it",
    "as",
    "from",
}


def tokenize_words(text: str, remove_stopwords: bool = False) -> list[str]:
    tokens = re.findall(r"[a-z0-9$]+", (text or "").lower())
    if remove_stopwords:
        return [t for t in tokens if t not in STOPWORDS]
    return tokens


def ngram_jaccard_similarity(text_a: str, text_b: str, n: int = 3) -> float:
    """Compute word-level n-gram Jaccard similarity in [0.0, 1.0]."""
    words_a = tokenize_words(text_a)
    words_b = tokenize_words(text_b)
    if not words_a or not words_b:
        return 0.0
    if len(words_a) < n or len(words_b) < n:
        set_a, set_b = set(words_a), set(words_b)
    else:
        set_a = {tuple(words_a[i : i + n]) for i in range(len(words_a) - n + 1)}
        set_b = {tuple(words_b[i : i + n]) for i in range(len(words_b) - n + 1)}
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return len(set_a & set_b) / union


def tfidf_cosine_similarity(text_a: str, text_b: str, corpus: list[str] | None = None) -> float:
    """Compute lightweight local TF-IDF cosine similarity between two texts."""
    tokens_a = tokenize_words(text_a, remove_stopwords=True)
    tokens_b = tokenize_words(text_b, remove_stopwords=True)
    if not tokens_a or not tokens_b:
        return 0.0

    docs = [tokens_a, tokens_b]
    if corpus:
        docs.extend(tokenize_words(doc, remove_stopwords=True) for doc in corpus[:50] if doc)

    num_docs = len(docs)
    df: Counter[str] = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1

    vocab = set(tokens_a) | set(tokens_b)
    tf_a = Counter(tokens_a)
    tf_b = Counter(tokens_b)

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for term in vocab:
        idf = math.log((1.0 + num_docs) / (1.0 + df[term])) + 1.0
        wa = (tf_a[term] / len(tokens_a)) * idf
        wb = (tf_b[term] / len(tokens_b)) * idf
        dot += wa * wb
        norm_a += wa * wa
        norm_b += wb * wb

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom <= 1e-9:
        return 0.0
    return min(1.0, max(0.0, dot / denom))


def compute_pair_similarity(
    candidate: ScriptGenerationSchema,
    prior_script: str,
    prior_hook: str,
    prior_titles: list[str],
    prior_summary: str,
    corpus: list[str] | None = None,
) -> dict[str, float]:
    """Compare candidate script against a historical record across script, hook, titles, and story angle."""
    ngram_sim = ngram_jaccard_similarity(candidate.script, prior_script, n=3)
    fuzz_script = fuzz.token_sort_ratio(candidate.script.lower(), prior_script.lower()) / 100.0
    tfidf_sim = tfidf_cosine_similarity(candidate.script, prior_script, corpus=corpus)

    hook_sim = fuzz.token_sort_ratio(candidate.hook.lower(), prior_hook.lower()) / 100.0 if prior_hook else 0.0
    angle_sim = (
        tfidf_cosine_similarity(candidate.event_summary, prior_summary, corpus=corpus)
        if prior_summary
        else 0.0
    )

    title_sim = 0.0
    if candidate.title_options and prior_titles:
        title_sim = max(
            fuzz.token_sort_ratio(t1.lower(), t2.lower()) / 100.0
            for t1 in candidate.title_options
            for t2 in prior_titles
        )

    composite = max(
        ngram_sim,
        0.45 * fuzz_script + 0.35 * tfidf_sim + 0.20 * max(hook_sim, angle_sim, title_sim),
    )
    return {
        "composite": round(composite, 4),
        "ngram": round(ngram_sim, 4),
        "fuzzy": round(fuzz_script, 4),
        "tfidf": round(tfidf_sim, 4),
        "hook": round(hook_sim, 4),
        "title": round(title_sim, 4),
        "angle": round(angle_sim, 4),
    }


def check_script_originality(
    candidate: ScriptGenerationSchema,
    threshold: float = 0.78,
    exclude_job_id: str | None = None,
) -> dict[str, Any]:
    """
    Compare candidate script against all historical scripts in SQLite.
    Returns dict with keys: passed, max_similarity, matched_script_id, breakdown.
    """
    with get_session() as session:
        stmt = select(ScriptRecord).order_by(ScriptRecord.id.desc()).limit(300)
        records = list(session.exec(stmt).all())

    if exclude_job_id:
        records = [r for r in records if r.job_id != exclude_job_id]

    if not records:
        return {
            "passed": True,
            "max_similarity": 0.0,
            "matched_script_id": None,
            "threshold": threshold,
            "breakdown": {},
        }

    corpus = [r.script_text for r in records if r.script_text]
    max_sim = 0.0
    matched_id: int | None = None
    best_breakdown: dict[str, float] = {}

    import json

    for rec in records:
        try:
            prior_titles = json.loads(rec.title_options_json or "[]")
        except Exception:
            prior_titles = []

        metrics = compute_pair_similarity(
            candidate=candidate,
            prior_script=rec.script_text,
            prior_hook=rec.hook,
            prior_titles=prior_titles,
            prior_summary=rec.event_summary,
            corpus=corpus,
        )
        if metrics["composite"] > max_sim:
            max_sim = metrics["composite"]
            matched_id = rec.id
            best_breakdown = metrics

    passed = max_sim <= threshold
    return {
        "passed": passed,
        "max_similarity": round(max_sim, 4),
        "matched_script_id": matched_id,
        "threshold": threshold,
        "breakdown": best_breakdown,
    }
