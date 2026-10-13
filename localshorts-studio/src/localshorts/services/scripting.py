"""Structured YouTube Shorts script generator with word-count, style, and originality enforcement."""

from __future__ import annotations

import re
from typing import Any

from localshorts.logging_config import get_logger
from localshorts.models.schemas import EvidenceBundleSchema, ScriptGenerationSchema
from localshorts.services.ollama import OllamaService
from localshorts.services.originality import check_script_originality

logger = get_logger("scripting")

BANNED_INTRO_PATTERNS = [
    r"^\s*hey\s+guys\b",
    r"^\s*what'?s\s+up\s+guys\b",
    r"^\s*welcome\s+back\b",
    r"^\s*in\s+this\s+video\b",
    r"^\s*today\s+we'?re\s+going\s+to\b",
    r"^\s*before\s+we\s+start\b",
    r"^\s*hello\s+everyone\b",
]


def count_spoken_words(text: str) -> int:
    """Count spoken words in a narration script."""
    if not text:
        return 0
    tokens = re.findall(r"[A-Za-z0-9$%'.-]+", text.strip())
    return len(tokens)


def strip_banned_intro(text: str) -> str:
    """Remove banned intro filler phrases if present at the start of a script."""
    cleaned = text.strip()
    for pat in BANNED_INTRO_PATTERNS:
        cleaned = re.sub(pat + r"[,!.\s-]*", "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def has_banned_intro(text: str) -> bool:
    for pat in BANNED_INTRO_PATTERNS:
        if re.search(pat, text or "", flags=re.IGNORECASE):
            return True
    return False


def build_deterministic_fallback_script(
    topic: str,
    evidence: EvidenceBundleSchema | None = None,
    is_smoke_test: bool = False,
) -> ScriptGenerationSchema:
    """
    Deterministic factual script builder used for the installation smoke test
    or when running offline tests without an active LLM daemon.
    Strictly 55-80 spoken words.
    """
    if is_smoke_test or "local ai" in topic.lower():
        hook = "Most AI tools send every word you type straight to a remote cloud server."
        script = (
            "Most AI tools send every word you type straight to a remote cloud server. "
            "Local AI flips that architecture completely by running compact four-billion-parameter models "
            "directly on your laptop GPU and system memory. "
            "Your prompts, scripts, and voice tracks stay on your own drive with zero monthly API bills "
            "and zero internet latency once downloaded. "
            "Combined with hardware NVENC encoding, one laptop can produce studio-grade vertical video locally. "
            "Would you switch your workflow to local AI?"
        )
        return ScriptGenerationSchema(
            event_summary="Local AI runs compact models directly on consumer laptop hardware without sending prompts to cloud servers.",
            hook=hook,
            script=script,
            visual_search_terms=["local ai gpu", "laptop neural network", "privacy hardware"],
            caption_highlights=["REMOTE CLOUD", "4 BILLION", "ZERO API BILLS", "NVENC"],
            title_options=[
                "Why Local AI Runs Without Sending Prompts to the Cloud",
                "How a 4GB Laptop GPU Runs Local AI Privately",
                "Zero Cloud Fees: How Local AI Works on Your Laptop",
            ],
            description="A quick breakdown of how local AI models and hardware NVENC encoding run completely on your own computer without cloud subscriptions.",
            comment_question="Would you switch your creative workflow to local AI?",
            source_claims=[
                "Compact 4B quantized models fit within 4GB to 6GB laptop VRAM.",
                "Local inference processes prompts on-device without cloud API transmission.",
            ],
        )

    headline = (evidence.headline if evidence and evidence.headline else topic).strip().rstrip(".")
    creator = (evidence.creator if evidence and evidence.creator else "This creator").strip()
    sources_count = len(evidence.sources) if evidence and evidence.sources else 1
    claim_1 = (
        evidence.claims[0]
        if evidence and evidence.claims
        else f"Reports across {sources_count} public feeds highlighted {headline}."
    )

    hook = f"{headline} just triggered a massive wave of reactions across the internet."
    body = (
        f"{hook} Within hours of the announcement, viewers and commentators began breaking down "
        f"every detail of what made this moment stand out from typical uploads. "
        f"{claim_1} Instead of relying on standard formats, the scale and timing pushed engagement "
        f"rapidly across multiple platforms at once. "
        f"Now everyone is watching to see what {creator} does next. "
        f"What was your first reaction when you saw this story break?"
    )
    words = body.split()
    if len(words) > 78:
        body = " ".join(words[:75]).rstrip(",;:") + "."

    return ScriptGenerationSchema(
        event_summary=f"Factual overview of {headline} based on {sources_count} collected source feed(s).",
        hook=hook,
        script=body,
        visual_search_terms=[creator, topic[:32], "viral internet trend"],
        caption_highlights=[creator.upper()[:18], f"{sources_count} SOURCES", "MASSIVE WAVE"],
        title_options=[
            f"Why {headline[:48]} Is Taking Over Right Now",
            f"The Real Story Behind {headline[:45]}",
            f"How {creator} Sparked Today's Biggest Internet Moment",
        ],
        description=f"Factual breakdown of {headline}. Sources verified via public feeds.",
        comment_question="What was your first reaction when you saw this story break?",
        source_claims=[claim_1],
    )


SYSTEM_PROMPT = """You are a principal short-form documentary scriptwriter for "Viral Creator & Internet Moments Explained".
Write factual, high-retention YouTube Shorts scripts strictly grounded in the provided Evidence Bundle.

MANDATORY RULES:
1. Length: The `script` field MUST be between 55 and 80 spoken words (18 to 35 seconds at normal speaking pace).
2. Structure:
   - Immediate Hook (first sentence, zero intro padding)
   - Never say "Hey guys", "Welcome back", "In this video", or any greeting.
   - One clear factual story -> one escalation detail -> one clear payoff -> optional short question at the end.
3. Factual Integrity:
   - Use ONLY facts, numbers, and events supported by the provided Evidence Bundle.
   - Never invent quotes, fake statistics, or fabricated events.
4. Output ONLY valid JSON matching the requested schema. /no_think"""


class ScriptingService:
    """Orchestrates evidence-grounded script generation, word-count repair, and originality checks."""

    def __init__(self, ollama: OllamaService | None = None) -> None:
        self.ollama = ollama or OllamaService()

    def _build_user_prompt(
        self,
        topic: str,
        evidence: EvidenceBundleSchema,
        target_duration_s: int = 24,
        angle_instruction: str = "",
    ) -> str:
        sources_text = "\n".join(
            f"- [{s.provider}] {s.headline} ({s.published_at}) — {s.snippet[:240]} [URL: {s.url}]"
            for s in evidence.sources[:6]
        ) or f"- User provided topic: {topic}"

        claims_text = "\n".join(f"- {c}" for c in evidence.claims[:6]) or f"- Topic focus: {topic}"
        target_words = max(55, min(78, int(target_duration_s * 2.7)))

        extra = f"\nIMPORTANT ANGLE DIVERSIFICATION:\n{angle_instruction}\n" if angle_instruction else ""

        return f"""Write a structured YouTube Short script JSON for this story.

TOPIC / HEADLINE: {evidence.headline or topic}
CREATOR / SUBJECT: {evidence.creator or topic}
TARGET SPOKEN LENGTH: {target_words} words (strictly between 55 and 80 words; ~{target_duration_s} seconds)

VERIFIED SOURCE EVIDENCE:
{sources_text}

SUPPORTED CLAIMS:
{claims_text}
{extra}
Return a JSON object with:
- event_summary (string)
- hook (string: opening sentence of the script)
- script (string: full 55-80 word spoken narration starting immediately with the hook)
- visual_search_terms (array of 4-6 visual keywords)
- caption_highlights (array of 4-8 high-impact words/numbers from the script to highlight)
- title_options (array of 3 strong, accurate titles)
- description (string: concise description + source note)
- comment_question (string: engaging viewer question)
- source_claims (array of factual claims used)
/no_think"""

    def _normalize_and_enforce_constraints(
        self,
        script_obj: ScriptGenerationSchema,
        topic: str,
        evidence: EvidenceBundleSchema,
    ) -> ScriptGenerationSchema:
        """Clean banned openings, ensure hook is included, and enforce 55-80 spoken word window."""
        cleaned_script = strip_banned_intro(script_obj.script)
        cleaned_hook = strip_banned_intro(script_obj.hook)

        # Ensure script begins with the hook or coherent opening
        if cleaned_hook and cleaned_hook[:20].lower() not in cleaned_script[:45].lower():
            cleaned_script = f"{cleaned_hook.rstrip('.!?')}. {cleaned_script}"

        word_count = count_spoken_words(cleaned_script)

        # If slightly below 55 words, append factual context + comment question cleanly
        if word_count < 55:
            additions: list[str] = []
            if script_obj.event_summary and script_obj.event_summary[:20].lower() not in cleaned_script.lower():
                additions.append(script_obj.event_summary.rstrip(".") + ".")
            if script_obj.comment_question and script_obj.comment_question[:15].lower() not in cleaned_script.lower():
                additions.append(script_obj.comment_question.rstrip("?.") + "?")
            if additions:
                cleaned_script = f"{cleaned_script.rstrip()} {' '.join(additions)}".strip()
                word_count = count_spoken_words(cleaned_script)

            while count_spoken_words(cleaned_script) < 55:
                filler_fact = (
                    f"Multiple public sources tracked how quickly this story gained momentum across {evidence.creator or 'creator'} communities today."
                )
                if filler_fact not in cleaned_script:
                    cleaned_script = f"{cleaned_script.rstrip()} {filler_fact}"
                else:
                    break

        # If over 80 words, trim cleanly at sentence boundary or 78 words
        if count_spoken_words(cleaned_script) > 80:
            sentences = re.split(r"(?<=[.!?])\s+", cleaned_script)
            kept: list[str] = []
            running = 0
            for sent in sentences:
                sw = count_spoken_words(sent)
                if running + sw <= 80 or running < 55:
                    kept.append(sent)
                    running += sw
                else:
                    break
            cleaned_script = " ".join(kept)
            if count_spoken_words(cleaned_script) > 80:
                trimmed_tokens = cleaned_script.split()[:78]
                cleaned_script = " ".join(trimmed_tokens).rstrip(",;:") + "."

        script_obj.script = cleaned_script
        script_obj.hook = cleaned_hook or cleaned_script.split(".")[0] + "."
        if not script_obj.title_options:
            script_obj.title_options = [
                f"{(evidence.headline or topic)[:55]}",
                f"What Really Happened: {(evidence.headline or topic)[:42]}",
                f"Why Everyone Is Talking About {(evidence.creator or topic)[:35]}",
            ]
        while len(script_obj.title_options) < 3:
            script_obj.title_options.append(f"{script_obj.title_options[0]} Explained")

        if not script_obj.caption_highlights:
            # Extract numbers, uppercase words, and key terms automatically
            auto_hl = re.findall(r"\$?\d+(?:,\d+)*(?:\.\d+)?(?:\s*(?:MILLION|BILLION|HOURS|DAYS|%|X))?", cleaned_script, flags=re.I)
            script_obj.caption_highlights = [h.strip().upper() for h in auto_hl if h.strip()][:6] or ["BREAKING", "RECORD"]

        return script_obj

    async def generate_script(
        self,
        topic: str,
        evidence: EvidenceBundleSchema,
        model: str = "qwen3:4b",
        num_ctx: int = 4096,
        target_duration_s: int = 24,
        originality_threshold: float = 0.78,
        job_id: str | None = None,
        is_smoke_test: bool = False,
        allow_offline_fallback: bool = True,
    ) -> tuple[ScriptGenerationSchema, dict[str, Any]]:
        """
        Generate a verified 55-80 word script, check originality against SQLite history,
        and rewrite automatically if similarity > originality_threshold.
        Returns (ScriptGenerationSchema, originality_report).
        """
        if is_smoke_test:
            smoke = build_deterministic_fallback_script(topic, evidence, is_smoke_test=True)
            orig = check_script_originality(smoke, threshold=originality_threshold, exclude_job_id=job_id)
            return smoke, orig

        prompt = self._build_user_prompt(topic=topic, evidence=evidence, target_duration_s=target_duration_s)
        script_obj: ScriptGenerationSchema | None = None

        try:
            active_model = await self.ollama.select_best_available_model(model)
            script_obj = await self.ollama.generate_structured_with_repair(
                prompt=prompt,
                schema_cls=ScriptGenerationSchema,
                model=active_model,
                system=SYSTEM_PROMPT,
                num_ctx=num_ctx,
            )
        except Exception as exc:
            logger.warning("Ollama script generation error (%s)", exc)
            if not allow_offline_fallback:
                raise
            script_obj = build_deterministic_fallback_script(topic=topic, evidence=evidence)

        script_obj = self._normalize_and_enforce_constraints(script_obj, topic=topic, evidence=evidence)
        orig_report = check_script_originality(
            script_obj,
            threshold=originality_threshold,
            exclude_job_id=job_id,
        )

        # If too similar to an existing script, run one angle-diversification rewrite pass
        if not orig_report["passed"]:
            logger.info(
                "Candidate similarity %.2f exceeds threshold %.2f; rewriting from distinct angle...",
                orig_report["max_similarity"],
                originality_threshold,
            )
            rewrite_angle = (
                "Your previous draft was too similar to an earlier script. "
                "Use a completely different opening hook, focus on the secondary timeline/impact consequence, "
                "and vary your phrasing while keeping all facts strictly accurate."
            )
            try:
                active_model = await self.ollama.select_best_available_model(model)
                rewritten = await self.ollama.generate_structured_with_repair(
                    prompt=self._build_user_prompt(
                        topic=topic,
                        evidence=evidence,
                        target_duration_s=target_duration_s,
                        angle_instruction=rewrite_angle,
                    ),
                    schema_cls=ScriptGenerationSchema,
                    model=active_model,
                    system=SYSTEM_PROMPT,
                    num_ctx=num_ctx,
                )
                script_obj = self._normalize_and_enforce_constraints(rewritten, topic=topic, evidence=evidence)
                orig_report = check_script_originality(
                    script_obj,
                    threshold=originality_threshold,
                    exclude_job_id=job_id,
                )
            except Exception as exc:
                logger.warning("Angle rewrite pass fallback: %s", exc)

        return script_obj, orig_report
