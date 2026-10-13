"""Scene Planner: maps spoken clauses into 8-15 dynamic visual scenes with controlled punch-zooms and SFX."""

from __future__ import annotations

import re
from typing import Sequence

from localshorts.logging_config import get_logger
from localshorts.models.schemas import (
    EvidenceBundleSchema,
    ScenePlanItemSchema,
    ScriptGenerationSchema,
)
from localshorts.services.tts import ClauseTiming

logger = get_logger("scene_planner")

VISUAL_TYPES_ROTATION = [
    "headline_card",
    "media_composite",
    "stat_callout",
    "evidence_card",
    "timeline_card",
    "quote_card",
    "payoff_card",
]


def _extract_numeric_focus(text: str, highlights: Sequence[str]) -> list[str]:
    found: list[str] = []
    for hl in highlights:
        if hl.lower() in text.lower():
            found.append(hl.upper())
    nums = re.findall(
        r"\$?\d+(?:,\d+)*(?:\.\d+)?(?:\s*(?:million|billion|thousand|hours|days|people|times|%|x|gb|mb))?",
        text,
        flags=re.IGNORECASE,
    )
    for n in nums:
        clean_n = n.strip().upper()
        if clean_n and clean_n not in found:
            found.append(clean_n)
    return found[:3]


def build_scene_plan(
    clauses: Sequence[ClauseTiming],
    script_obj: ScriptGenerationSchema,
    evidence: EvidenceBundleSchema | None = None,
) -> list[ScenePlanItemSchema]:
    """
    Map spoken clauses into 8-15 scenes (each ~1.5-3.2s) with:
    - Non-repeating adjacent visual types
    - 2-5 meaningful punch-zooms (1.00 -> 1.05 / 1.08 / 1.12) on hook, numbers, and payoff
    - Subtle transition SFX (impact, pop, whoosh, riser)
    """
    if not clauses:
        return []

    # Merge ultra-short (< 1.15s) clauses so scenes stay between ~1.4s and 3.5s
    merged_clauses: list[ClauseTiming] = []
    idx = 0
    while idx < len(clauses):
        curr = clauses[idx]
        if curr.duration < 1.15 and idx + 1 < len(clauses) and len(clauses) - len(merged_clauses) > 6:
            nxt = clauses[idx + 1]
            merged_clauses.append(
                ClauseTiming(
                    index=len(merged_clauses),
                    text=f"{curr.text} {nxt.text}",
                    start=curr.start,
                    end=nxt.end,
                    duration=round(nxt.end - curr.start, 3),
                )
            )
            idx += 2
        else:
            merged_clauses.append(
                ClauseTiming(
                    index=len(merged_clauses),
                    text=curr.text,
                    start=curr.start,
                    end=curr.end,
                    duration=round(curr.end - curr.start, 3),
                )
            )
            idx += 1

    total_scenes = len(merged_clauses)
    search_terms = list(script_obj.visual_search_terms or [])
    if not search_terms:
        search_terms = [ evidence.creator or evidence.topic or "viral story" ] if evidence else ["technology"]

    scenes: list[ScenePlanItemSchema] = []
    prev_visual_type = ""
    punch_zoom_count = 0
    max_punch_zooms = min(5, max(2, total_scenes // 2))

    for s_idx, clause in enumerate(merged_clauses):
        emphasis = _extract_numeric_focus(clause.text, script_obj.caption_highlights)
        has_number = bool(re.search(r"\d", clause.text))

        # Determine scene narrative purpose
        if s_idx == 0:
            purpose = "hook"
            v_type = "headline_card"
        elif s_idx == total_scenes - 1:
            purpose = "question" if "?" in clause.text else "payoff"
            v_type = "payoff_card"
        elif s_idx == total_scenes - 2:
            purpose = "payoff"
            v_type = "stat_callout" if has_number else "quote_card"
        elif has_number:
            purpose = "stat_callout"
            v_type = "stat_callout"
        elif s_idx % 3 == 1:
            purpose = "context"
            v_type = "media_composite"
        elif s_idx % 3 == 2:
            purpose = "evidence"
            v_type = "evidence_card"
        else:
            purpose = "escalation"
            v_type = "timeline_card"

        # Guarantee adjacent visual_type diversity
        if v_type == prev_visual_type:
            for alt in VISUAL_TYPES_ROTATION:
                if alt != prev_visual_type:
                    v_type = alt
                    break
        prev_visual_type = v_type

        # Assign controlled motion & punch zoom (capped at 2-5 per Short)
        is_key_beat = s_idx == 0 or purpose in ("stat_callout", "payoff")
        if is_key_beat and punch_zoom_count < max_punch_zooms:
            punch_zoom_count += 1
            if s_idx == 0:
                motion = "punch_zoom_mid"
                z_start, z_end = 1.00, 1.08
            elif purpose == "payoff":
                motion = "punch_zoom_strong"
                z_start, z_end = 1.00, 1.12
            else:
                motion = "punch_zoom_in"
                z_start, z_end = 1.00, 1.05
        else:
            motion = "slow_pan_up" if s_idx % 2 == 0 else "slow_drift"
            z_start, z_end = 1.00, 1.03

        # Ensure at least 2 punch zooms if the video has >= 2 scenes
        if s_idx == total_scenes - 1 and punch_zoom_count < 2:
            punch_zoom_count += 1
            motion = "punch_zoom_mid"
            z_start, z_end = 1.00, 1.08

        # Assign subtle SFX on key transitions (max ~4 per Short)
        sfx = ""
        if s_idx == 0:
            sfx = "impact"
        elif purpose == "stat_callout" and punch_zoom_count <= 3:
            sfx = "pop"
        elif s_idx == total_scenes - 2 and total_scenes >= 4:
            sfx = "riser"
        elif s_idx == total_scenes // 2:
            sfx = "whoosh"

        query = search_terms[s_idx % len(search_terms)]
        scenes.append(
            ScenePlanItemSchema(
                scene_index=s_idx,
                start=clause.start,
                end=clause.end,
                duration=max(0.5, clause.duration),
                purpose=purpose,
                spoken_text=clause.text,
                visual_type=v_type,
                visual_query=query,
                caption_emphasis=emphasis,
                motion=motion,
                zoom_start=z_start,
                zoom_end=z_end,
                sfx=sfx,
            )
        )

    logger.info(
        "Planned %d scenes (%d punch zooms) covering %.2fs",
        len(scenes),
        punch_zoom_count,
        scenes[-1].end if scenes else 0.0,
    )
    return scenes
