"""Unit tests for 2-5 word caption card segmentation, keyword emphasis, and ASS/SRT generation."""

from __future__ import annotations

from pathlib import Path

from localshorts.services.captions import (
    build_caption_cards,
    detect_emphasized_tokens,
    write_ass_file,
    write_srt_file,
)
from localshorts.services.tts import ClauseTiming


def test_detect_emphasized_tokens_caps_highlights() -> None:
    words = ["Over", "500,000", "viewers", "watched", "live"]
    emph = detect_emphasized_tokens(words, caption_highlights=["500,000", "LIVE"])
    assert 1 <= len(emph) <= 2
    assert "500,000" in emph


def test_build_caption_cards_and_write_ass_srt(tmp_path: Path) -> None:
    clauses = [
        ClauseTiming(
            index=0,
            text="Most AI tools send every word you type straight to a remote cloud server.",
            start=0.0,
            end=4.2,
            duration=4.2,
        ),
        ClauseTiming(
            index=1,
            text="Local AI runs compact 4 billion parameter models directly on your laptop GPU.",
            start=4.2,
            end=8.6,
            duration=4.4,
        ),
    ]
    cards = build_caption_cards(clauses, caption_highlights=["4 BILLION", "REMOTE CLOUD"], max_words_per_card=4)
    assert len(cards) >= 5
    for card in cards:
        assert 1 <= len(card.words) <= 5
        assert card.end > card.start

    srt_file = write_srt_file(cards, tmp_path / "captions.srt")
    ass_file = write_ass_file(cards, tmp_path / "captions.ass", preset="Bold")

    assert srt_file.exists()
    assert "-->" in srt_file.read_text(encoding="utf-8")
    assert ass_file.exists()
    ass_content = ass_file.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass_content
    assert "PlayResY: 1920" in ass_content
    assert "Dialogue:" in ass_content
