"""First-class 1080x1920 procedural motion-graphics plate compositor with 6 studio visual presets."""

from __future__ import annotations

import math
import textwrap
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from localshorts.config import FONTS_DIR
from localshorts.logging_config import get_logger
from localshorts.models.schemas import EvidenceBundleSchema, ScenePlanItemSchema

logger = get_logger("procedural")

# Render plates at 1080x1920 (9:16 vertical HD)
PLATE_WIDTH = 1080
PLATE_HEIGHT = 1920

PRESET_PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "Creator News": {
        "bg_top": (11, 16, 33),
        "bg_bottom": (22, 33, 62),
        "card_bg": (20, 30, 56),
        "card_border": (56, 189, 248),
        "accent": (250, 204, 21),
        "accent_alt": (56, 189, 248),
        "text_primary": (248, 250, 252),
        "text_muted": (148, 163, 184),
    },
    "Tech Explainer": {
        "bg_top": (8, 18, 26),
        "bg_bottom": (14, 38, 48),
        "card_bg": (15, 32, 43),
        "card_border": (16, 185, 129),
        "accent": (52, 211, 153),
        "accent_alt": (6, 182, 212),
        "text_primary": (240, 253, 244),
        "text_muted": (148, 163, 184),
    },
    "Breaking Internet": {
        "bg_top": (28, 10, 18),
        "bg_bottom": (48, 14, 28),
        "card_bg": (38, 16, 26),
        "card_border": (244, 63, 94),
        "accent": (251, 113, 133),
        "accent_alt": (250, 204, 21),
        "text_primary": (255, 241, 242),
        "text_muted": (253, 164, 175),
    },
    "Challenge": {
        "bg_top": (15, 23, 42),
        "bg_bottom": (49, 46, 129),
        "card_bg": (30, 41, 75),
        "card_border": (245, 158, 11),
        "accent": (251, 191, 36),
        "accent_alt": (96, 165, 250),
        "text_primary": (255, 255, 255),
        "text_muted": (203, 213, 225),
    },
    "Minimal Documentary": {
        "bg_top": (18, 18, 22),
        "bg_bottom": (32, 32, 38),
        "card_bg": (28, 28, 34),
        "card_border": (161, 161, 170),
        "accent": (228, 228, 231),
        "accent_alt": (161, 161, 170),
        "text_primary": (250, 250, 250),
        "text_muted": (161, 161, 170),
    },
    "High Energy": {
        "bg_top": (24, 9, 44),
        "bg_bottom": (59, 7, 100),
        "card_bg": (42, 14, 74),
        "card_border": (232, 121, 249),
        "accent": (0, 240, 255),
        "accent_alt": (250, 204, 21),
        "text_primary": (255, 255, 255),
        "text_muted": (216, 180, 254),
    },
}


def _load_font(size: int, bold: bool = True, condensed: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[Path] = []
    if condensed:
        candidates.append(FONTS_DIR / "BarlowCondensed-Bold.ttf")
        candidates.append(FONTS_DIR / "RobotoCondensed-Bold.ttf")
    if bold:
        candidates.append(FONTS_DIR / "ArchivoBlack-Regular.ttf")
        candidates.append(FONTS_DIR / "BarlowCondensed-Bold.ttf")
    candidates.append(FONTS_DIR / "RobotoCondensed-Bold.ttf")

    for font_path in candidates:
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except Exception:
                continue

    for sys_font in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf", "calibri.ttf"):
        try:
            return ImageFont.truetype(sys_font, size=size)
        except Exception:
            continue

    return ImageFont.load_default()


def _create_gradient_background(
    width: int,
    height: int,
    top_rgb: tuple[int, int, int],
    bottom_rgb: tuple[int, int, int],
    accent_rgb: tuple[int, int, int],
    scene_index: int = 0,
) -> Image.Image:
    """Generate a smooth vertical gradient with a subtle radial glow and geometric grid."""
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]

    top = np.array(top_rgb, dtype=np.float32)
    bot = np.array(bottom_rgb, dtype=np.float32)
    acc = np.array(accent_rgb, dtype=np.float32)

    base = top[None, None, :] * (1.0 - y[:, :, None]) + bot[None, None, :] * y[:, :, None]

    # Radial spotlight glow that shifts position per scene
    cx = 0.5 + 0.18 * math.sin(scene_index * 1.3)
    cy = 0.34 + 0.08 * math.cos(scene_index * 0.9)
    dist_sq = ((x - cx) * 1.1) ** 2 + ((y - cy) * 1.6) ** 2
    glow = np.exp(-dist_sq * 5.2)[:, :, None] * 0.24
    arr = np.clip(base + acc[None, None, :] * glow, 0, 255).astype(np.uint8)

    img = Image.fromarray(arr, mode="RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle architectural grid lines
    grid_col = (*accent_rgb, 18)
    step = 90
    offset = (scene_index * 18) % step
    for gx in range(offset, width, step):
        draw.line([(gx, 0), (gx, height)], fill=grid_col, width=1)
    for gy in range(offset, height, step):
        draw.line([(0, gy), (width, gy)], fill=grid_col, width=1)

    return img


def render_scene_plate(
    scene: ScenePlanItemSchema,
    total_scenes: int,
    output_path: Path,
    preset: str = "Creator News",
    topic_title: str = "",
    evidence: EvidenceBundleSchema | None = None,
) -> Path:
    """
    Render a rich 1080x1920 visual scene plate (compositing external/local media if attached,
    or rendering kinetic cards, stat callouts, timelines, and waveform bars).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    palette = PRESET_PALETTES.get(preset, PRESET_PALETTES["Creator News"])

    img = _create_gradient_background(
        PLATE_WIDTH,
        PLATE_HEIGHT,
        palette["bg_top"],
        palette["bg_bottom"],
        palette["accent_alt"],
        scene_index=scene.scene_index,
    )
    draw = ImageDraw.Draw(img, "RGBA")

    font_badge = _load_font(28, bold=True, condensed=True)
    font_title = _load_font(54, bold=True, condensed=False)
    font_stat = _load_font(96, bold=True, condensed=True)
    font_body = _load_font(40, bold=False, condensed=True)
    font_small = _load_font(24, bold=False, condensed=True)

    accent = palette["accent"]
    accent_alt = palette["accent_alt"]
    text_primary = palette["text_primary"]
    text_muted = palette["text_muted"]
    card_bg = (*palette["card_bg"], 225)
    card_border = (*palette["card_border"], 210)

    # 1. Top Header Bar (Y = 95..165) — Topic / Preset pill + Scene Progress Dots
    badge_label = (
        f"● {(evidence.creator or topic_title or preset).upper()[:28]}"
        if evidence and (evidence.creator or topic_title)
        else f"● {preset.upper()}"
    )
    draw.rounded_rectangle(
        [72, 100, 560, 162],
        radius=31,
        fill=(*palette["card_bg"], 235),
        outline=(*accent_alt, 180),
        width=2,
    )
    draw.text((102, 115), badge_label, font=font_badge, fill=accent)

    # Scene progress counter pill on top right
    prog_label = f"SCENE {scene.scene_index + 1}/{max(1, total_scenes)}"
    draw.rounded_rectangle(
        [800, 100, 1008, 162],
        radius=31,
        fill=(*palette["card_bg"], 210),
        outline=(*text_muted, 90),
        width=2,
    )
    draw.text((835, 115), prog_label, font=font_badge, fill=text_primary)

    # 2. Main Visual Content Zone (Y = 210..1240, keeping Y = 1320..1680 clean for ASS captions)
    has_external_image = False
    if scene.media_asset_path:
        media_p = Path(scene.media_asset_path)
        if media_p.exists() and media_p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            try:
                ext_img = Image.open(media_p).convert("RGB")
                # Center-crop/fit into 936x620 hero frame
                frame_w, frame_h = 936, 620
                ext_ratio = ext_img.width / float(max(1, ext_img.height))
                target_ratio = frame_w / float(frame_h)
                if ext_ratio > target_ratio:
                    new_h = frame_h
                    new_w = int(new_h * ext_ratio)
                else:
                    new_w = frame_w
                    new_h = int(new_w / max(0.1, ext_ratio))
                resized = ext_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                left = (new_w - frame_w) // 2
                top = (new_h - frame_h) // 2
                cropped = resized.crop((left, top, left + frame_w, top + frame_h))

                # Draw hero card frame at Y = 230..850
                draw.rounded_rectangle(
                    [66, 224, 1014, 856],
                    radius=28,
                    fill=card_bg,
                    outline=card_border,
                    width=4,
                )
                img.paste(cropped, (72, 230))
                has_external_image = True

                if scene.media_attribution:
                    draw.text(
                        (84, 868),
                        f"Source: {scene.media_attribution[:68]}",
                        font=font_small,
                        fill=text_muted,
                    )
            except Exception as exc:
                logger.debug("Could not composite media image %s: %s", media_p, exc)

    if has_external_image:
        # Context summary card below the hero image (Y = 915..1220)
        draw.rounded_rectangle(
            [72, 915, 1008, 1215],
            radius=28,
            fill=card_bg,
            outline=card_border,
            width=3,
        )
        focus_label = (
            scene.caption_emphasis[0]
            if scene.caption_emphasis
            else scene.purpose.replace("_", " ").upper()
        )
        draw.text((110, 942), f"KEY BEAT // {focus_label}", font=font_badge, fill=accent)
        wrapped = textwrap.wrap(scene.spoken_text, width=32)[:3]
        for l_idx, line in enumerate(wrapped):
            draw.text((110, 995 + l_idx * 62), line, font=font_body, fill=text_primary)
    else:
        # Pure Procedural Motion-Graphics Layout tailored to scene.visual_type
        vtype = scene.visual_type

        if vtype == "stat_callout" or scene.caption_emphasis:
            # Giant stat/keyword callout card (Y = 250..740)
            stat_text = (
                scene.caption_emphasis[0]
                if scene.caption_emphasis
                else scene.purpose.upper()
            )
            draw.rounded_rectangle(
                [72, 250, 1008, 730],
                radius=36,
                fill=card_bg,
                outline=(*accent, 235),
                width=5,
            )
            draw.rounded_rectangle(
                [112, 292, 420, 344],
                radius=14,
                fill=(*accent_alt, 45),
                outline=(*accent_alt, 180),
                width=2,
            )
            draw.text((134, 302), f"HIGHLIGHT // #{scene.scene_index + 1:02d}", font=font_badge, fill=accent_alt)

            # Fit stat text cleanly
            stat_font = font_stat if len(stat_text) <= 14 else _load_font(68, bold=True, condensed=True)
            draw.text((112, 395), stat_text[:22], font=stat_font, fill=accent)

            # Secondary context bar (Y = 780..1190)
            draw.rounded_rectangle(
                [72, 780, 1008, 1190],
                radius=30,
                fill=(*palette["card_bg"], 200),
                outline=card_border,
                width=3,
            )
            wrapped = textwrap.wrap(scene.spoken_text, width=26)[:4]
            for l_idx, line in enumerate(wrapped):
                draw.text((112, 830 + l_idx * 76), line, font=font_title, fill=text_primary)

        elif vtype in ("evidence_card", "timeline_card"):
            # Multi-source Verified Evidence / Timeline Card (Y = 240..1180)
            draw.rounded_rectangle(
                [72, 240, 1008, 1180],
                radius=34,
                fill=card_bg,
                outline=card_border,
                width=4,
            )
            header_tag = "VERIFIED SOURCE LOG" if vtype == "evidence_card" else "EVENT TIMELINE"
            draw.rectangle([72, 240, 1008, 330], fill=(*accent_alt, 45))
            draw.text((112, 268), f"■ {header_tag}", font=font_badge, fill=accent)

            wrapped = textwrap.wrap(scene.spoken_text, width=25)[:5]
            for l_idx, line in enumerate(wrapped):
                draw.text((112, 380 + l_idx * 80), line, font=font_title, fill=text_primary)

            # Source metadata footer inside card
            src_domain = "Local Evidence Manifest"
            if evidence and evidence.sources:
                src_item = evidence.sources[scene.scene_index % len(evidence.sources)]
                src_domain = f"{src_item.provider.upper()} · {src_item.domain or 'Verified Feed'}"
            draw.rounded_rectangle(
                [112, 1040, 968, 1125],
                radius=18,
                fill=(*palette["bg_top"], 220),
                outline=(*accent, 140),
                width=2,
            )
            draw.text((142, 1066), f"SOURCE: {src_domain[:52]}", font=font_badge, fill=text_muted)

        else:
            # Headline / Hook / Payoff Kinetic Card (Y = 250..1180)
            draw.rounded_rectangle(
                [72, 250, 1008, 1180],
                radius=36,
                fill=card_bg,
                outline=(*accent, 220) if scene.purpose in ("hook", "payoff") else card_border,
                width=4,
            )
            tag_str = {
                "hook": "BREAKING STORY",
                "payoff": "THE PAYOFF",
                "question": "JOIN THE DISCUSSION",
            }.get(scene.purpose, "STORY BREAKDOWN")

            draw.rounded_rectangle(
                [112, 300, 480, 360],
                radius=16,
                fill=(*accent, 38),
                outline=(*accent, 200),
                width=2,
            )
            draw.text((138, 314), tag_str, font=font_badge, fill=accent)

            wrapped = textwrap.wrap(scene.spoken_text, width=22)[:6]
            for l_idx, line in enumerate(wrapped):
                color = accent if (l_idx == 0 and scene.purpose == "hook") else text_primary
                draw.text((112, 415 + l_idx * 86), line, font=font_title, fill=color)

    # 3. Audio Waveform & Progress Accent Bar at Y = 1245..1310 (right above the caption zone)
    num_bars = 36
    bar_w = 14
    gap = 11
    start_x = (PLATE_WIDTH - (num_bars * (bar_w + gap))) // 2
    center_y = 1280
    for b_i in range(num_bars):
        phase = (scene.scene_index * 1.7) + (b_i * 0.42)
        h = int(12 + 28 * abs(math.sin(phase)) * (0.6 + 0.4 * math.cos(b_i * 0.2)))
        bx = start_x + b_i * (bar_w + gap)
        active = (b_i / float(num_bars)) <= ((scene.scene_index + 1) / float(max(1, total_scenes)))
        b_col = (*accent, 210) if active else (*text_muted, 70)
        draw.rounded_rectangle(
            [bx, center_y - h, bx + bar_w, center_y + h],
            radius=5,
            fill=b_col,
        )

    img.save(str(output_path), format="PNG", optimize=False)
    return output_path


def render_all_scene_plates(
    scenes: Sequence[ScenePlanItemSchema],
    plates_dir: Path,
    preset: str = "Creator News",
    topic_title: str = "",
    evidence: EvidenceBundleSchema | None = None,
) -> list[ScenePlanItemSchema]:
    """Render 1080x1920 PNG visual plates for all scenes in a job."""
    plates_dir.mkdir(parents=True, exist_ok=True)
    total = len(scenes)
    for scene in scenes:
        plate_path = plates_dir / f"scene_{scene.scene_index:02d}.png"
        render_scene_plate(
            scene=scene,
            total_scenes=total,
            output_path=plate_path,
            preset=preset,
            topic_title=topic_title,
            evidence=evidence,
        )
        scene.rendered_plate_path = str(plate_path)
    return list(scenes)
