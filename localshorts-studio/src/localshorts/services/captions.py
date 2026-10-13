"""Word/card caption timing engine, automatic keyword emphasis, and open-font ASS/SRT subtitle generator."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from localshorts.config import FONTS_DIR
from localshorts.logging_config import get_logger
from localshorts.models.schemas import CaptionCardSchema
from localshorts.services.tts import ClauseTiming

logger = get_logger("captions")

EMPHASIS_REGEX = re.compile(
    r"(?:\$\d[\d,.]*|\b\d+(?:,\d+)*(?:\.\d+)?(?:\s*(?:million|billion|thousand|hours|days|people|times|%|x))?\b"
    r"|\b(?:record|million|billion|first|biggest|insane|massive|live|secret|instant|zero)\b)",
    flags=re.IGNORECASE,
)


def detect_emphasized_tokens(
    words: list[str],
    caption_highlights: Sequence[str] | None = None,
) -> list[str]:
    """
    Identify 1-2 high-impact words in a 2-5 word caption card for visual emphasis.
    Never emphasizes every word in the card.
    """
    if not words:
        return []

    highlight_vocab: set[str] = set()
    for phrase in caption_highlights or []:
        for token in re.findall(r"[A-Za-z0-9$%]+", phrase.upper()):
            if len(token) >= 2:
                highlight_vocab.add(token)

    candidates: list[str] = []
    for w in words:
        clean_w = re.sub(r"[^A-Za-z0-9$%]", "", w).upper()
        if not clean_w:
            continue
        if clean_w in highlight_vocab or EMPHASIS_REGEX.search(w):
            candidates.append(w)

    # Cap at maximum 2 emphasized words per card (and never all words if len <= 2)
    max_allowed = 1 if len(words) <= 3 else 2
    return candidates[:max_allowed]


def build_caption_cards(
    clauses: Sequence[ClauseTiming],
    caption_highlights: Sequence[str] | None = None,
    max_words_per_card: int = 4,
) -> list[CaptionCardSchema]:
    """
    Segment spoken clauses into 2-5 word high-retention caption cards with
    syllable/character-weighted timing derived from the exact clause WAV durations.
    """
    max_words = max(2, min(5, max_words_per_card))
    cards: list[CaptionCardSchema] = []
    card_idx = 1

    for clause in clauses:
        words = [w for w in clause.text.strip().split() if w]
        if not words:
            continue

        # Partition clause words into chunks of 2 to max_words
        chunks: list[list[str]] = []
        i = 0
        while i < len(words):
            remaining = len(words) - i
            if remaining <= max_words:
                chunks.append(words[i:])
                break
            if remaining == max_words + 1 and max_words >= 3:
                chunks.append(words[i : i + (max_words - 1)])
                i += max_words - 1
            else:
                chunks.append(words[i : i + max_words])
                i += max_words

        # Weight each chunk's duration by character length + punctuation pause weight
        weights = [max(3, sum(len(w) + 1 for w in ch)) for ch in chunks]
        total_weight = float(sum(weights))
        cursor = clause.start

        for ch_idx, chunk_words in enumerate(chunks):
            frac = weights[ch_idx] / total_weight
            dur = clause.duration * frac if ch_idx < len(chunks) - 1 else max(0.18, clause.end - cursor)
            start_t = round(cursor, 3)
            end_t = round(min(clause.end, start_t + dur), 3)
            if end_t <= start_t:
                end_t = round(start_t + 0.20, 3)
            cursor = end_t

            emph = detect_emphasized_tokens(chunk_words, caption_highlights=caption_highlights)
            cards.append(
                CaptionCardSchema(
                    index=card_idx,
                    start=start_t,
                    end=end_t,
                    text=" ".join(chunk_words),
                    words=chunk_words,
                    emphasized_words=emph,
                )
            )
            card_idx += 1

    return cards


def _format_srt_timestamp(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    minutes = rem // 60_000
    rem = rem % 60_000
    secs = rem // 1000
    millis = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_ass_timestamp(seconds: float) -> str:
    cs_total = max(0, int(round(seconds * 100)))
    hours = cs_total // 360_000
    rem = cs_total % 360_000
    minutes = rem // 6_000
    rem = rem % 6_000
    secs = rem // 100
    centis = rem % 100
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _resolve_caption_font(preset: str) -> str:
    """Select bundled OFL open font name based on preset (with safe fallback)."""
    preset_norm = (preset or "Bold").strip().lower()
    if preset_norm == "high energy" and (FONTS_DIR / "BarlowCondensed-Bold.ttf").exists():
        return "Barlow Condensed"
    if preset_norm in ("bold", "high energy") and (FONTS_DIR / "ArchivoBlack-Regular.ttf").exists():
        return "Archivo Black"
    if (FONTS_DIR / "RobotoCondensed-Bold.ttf").exists():
        return "Roboto Condensed"
    return "Arial Black"


def write_srt_file(cards: Sequence[CaptionCardSchema], srt_path: Path) -> Path:
    """Write standard SubRip (.srt) subtitle file."""
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for card in cards:
        start_str = _format_srt_timestamp(card.start)
        end_str = _format_srt_timestamp(card.end)
        blocks.append(f"{card.index}\n{start_str} --> {end_str}\n{card.text}\n")
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    return srt_path


def write_ass_file(
    cards: Sequence[CaptionCardSchema],
    ass_path: Path,
    preset: str = "Bold",
) -> Path:
    """
    Write an Advanced SubStation Alpha (.ass) file styled for 1080x1920 vertical Shorts:
    - Positioned at centered lower-middle (MarginV=420, clear of YouTube UI zones)
    - High-contrast outline & shadow readable on 360px mobile screens
    - Automatic keyword highlight color & slight scale pop
    """
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    font_name = _resolve_caption_font(preset)
    preset_norm = (preset or "Bold").strip().lower()

    # ASS colors are &HAABBGGRR
    if preset_norm == "clean":
        font_size = 66
        primary_col = "&H00FFFFFF"
        highlight_col = "&H00FFE600"  # Cyan-Yellow
        outline_col = "&H00141414"
        back_col = "&H64000000"
        border_style = 1
        outline_px = 5
        shadow_px = 3
        uppercase = False
    elif preset_norm == "minimal":
        font_size = 60
        primary_col = "&H00F8FAFC"
        highlight_col = "&H0060D5FF"
        outline_col = "&H30101520"
        back_col = "&H80000000"
        border_style = 3  # Opaque box
        outline_px = 14
        shadow_px = 0
        uppercase = False
    elif preset_norm == "high energy":
        font_size = 76
        primary_col = "&H00FFFFFF"
        highlight_col = "&H0000EAFF"  # Bright Electric Gold/Yellow
        outline_col = "&H00080808"
        back_col = "&H80000000"
        border_style = 1
        outline_px = 7
        shadow_px = 4
        uppercase = True
    else:  # Default "Bold"
        font_size = 72
        primary_col = "&H00FFFFFF"
        highlight_col = "&H0022E5FF"  # Vibrant Gold (&H00BBGGRR: R=FF, G=E5, B=22)
        outline_col = "&H000A0A0A"
        back_col = "&H70000000"
        border_style = 1
        outline_px = 6
        shadow_px = 3
        uppercase = True

    header = f"""[Script Info]
Title: LocalShorts Studio Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_col},{highlight_col},{outline_col},{back_col},-1,0,0,0,100,100,1,0,{border_style},{outline_px},{shadow_px},2,90,90,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = [header]
    for card in cards:
        start_ass = _format_ass_timestamp(card.start)
        end_ass = _format_ass_timestamp(card.end)
        emph_set = {w.upper() for w in card.emphasized_words}

        formatted_tokens: list[str] = []
        for word in card.words:
            display_w = word.upper() if uppercase else word
            # Escape ASS special braces
            display_w = display_w.replace("{", "(").replace("}", ")")
            if word.upper() in emph_set:
                formatted_tokens.append(
                    f"{{\\c{highlight_col}\\fscx110\\fscy110}}{display_w}{{\\c{primary_col}\\fscx100\\fscy100}}"
                )
            else:
                formatted_tokens.append(display_w)

        # Pop-in micro-animation for Bold / High Energy
        anim_prefix = "{\\fscx95\\fscy95\\t(0,75,\\fscx100\\fscy100)}" if preset_norm in ("bold", "high energy") else ""
        dialogue_text = anim_prefix + " ".join(formatted_tokens)
        lines.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{dialogue_text}")

    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ass_path


def generate_captions_for_job(
    clauses: Sequence[ClauseTiming],
    caption_highlights: Sequence[str],
    output_dir: Path,
    preset: str = "Bold",
) -> tuple[list[CaptionCardSchema], Path, Path]:
    """Generate caption cards and write both captions.ass and captions.srt into output_dir."""
    cards = build_caption_cards(clauses=clauses, caption_highlights=caption_highlights)
    ass_path = write_ass_file(cards=cards, ass_path=output_dir / "captions.ass", preset=preset)
    srt_path = write_srt_file(cards=cards, srt_path=output_dir / "captions.srt")
    logger.info(
        "Generated %d caption cards [preset=%s] -> %s, %s",
        len(cards),
        preset,
        ass_path.name,
        srt_path.name,
    )
    return cards, ass_path, srt_path
