"""Unit tests for script word count enforcement, banned intro stripping, and deterministic fallback."""

from __future__ import annotations

from localshorts.services.research import build_evidence_bundle
from localshorts.services.scripting import (
    ScriptingService,
    build_deterministic_fallback_script,
    count_spoken_words,
    has_banned_intro,
    strip_banned_intro,
)


def test_strip_banned_intro_removes_greetings() -> None:
    raw = "Hey guys, welcome back! MrBeast just built a massive 100-camera studio in 48 hours."
    cleaned = strip_banned_intro(raw)
    assert not has_banned_intro(cleaned)
    assert "Hey guys" not in cleaned


def test_smoke_test_script_word_count_in_55_to_80_window() -> None:
    script_obj = build_deterministic_fallback_script(
        topic="Why local AI can run without sending your prompt to the cloud.",
        is_smoke_test=True,
    )
    wc = count_spoken_words(script_obj.script)
    assert 55 <= wc <= 80
    assert not has_banned_intro(script_obj.script)
    assert len(script_obj.title_options) >= 3


def test_normalize_enforces_55_to_80_word_bounds() -> None:
    svc = ScriptingService()
    evidence = build_evidence_bundle(
        topic="IShowSpeed Stream",
        headline="IShowSpeed Breaks Concurrent Viewer Record in Europe",
        creator="IShowSpeed",
    )
    # Start with a short script (<55 words) and verify normalization expands it into [55, 80]
    short_obj = build_deterministic_fallback_script("IShowSpeed Stream", evidence=evidence)
    short_obj.script = "Hey guys! IShowSpeed broke his streaming record during a live broadcast in Europe today."
    normalized = svc._normalize_and_enforce_constraints(short_obj, "IShowSpeed Stream", evidence)
    wc = count_spoken_words(normalized.script)
    assert 55 <= wc <= 80
    assert not has_banned_intro(normalized.script)
