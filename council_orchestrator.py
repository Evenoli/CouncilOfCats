"""Council debate pipeline: pre-analysis, turn loop, post-review."""

from __future__ import annotations

import logging
import threading
from typing import Any

from council_common import (
    CouncilBrief,
    LLMClient,
    append_transcript_line,
    dispatch_cat_message,
    ensure_research_dirs,
    format_prompt,
    get_debate_cats,
    get_display_name,
    load_cat_system_prompt,
    read_prompt,
    save_transcript,
)
from hermes_research import post_council_review, pre_council_analysis

logger = logging.getLogger(__name__)


def generate_turn(
    llm: LLMClient,
    cat_slug: str,
    user_prompt: str,
) -> str:
    system_prompt = load_cat_system_prompt(cat_slug)
    return llm.chat(system_prompt, user_prompt)


def run_debate_loop(
    config: dict[str, Any],
    llm: LLMClient,
    brief: CouncilBrief,
) -> str:
    delay = float(config.get("council", {}).get("webhook_delay_seconds", 2))
    transcript = ""

    intro_prompt = format_prompt(
        read_prompt("chair_intro_user.md"),
        summary=brief.summary,
        memory_callbacks=brief.memory_callbacks,
    )
    chair_name = get_display_name(config, "chair-cat")
    chair_intro = generate_turn(llm, "chair-cat", intro_prompt)
    transcript = append_transcript_line(transcript, chair_name, chair_intro)
    dispatch_cat_message(config, "chair-cat", chair_intro, delay)

    for _round in range(2):
        for cat_slug in get_debate_cats(config):
            turn_prompt = format_prompt(
                read_prompt("debate_turn_user.md"),
                transcript=transcript,
                display_name=get_display_name(config, cat_slug),
            )
            response = generate_turn(llm, cat_slug, turn_prompt)
            transcript = append_transcript_line(
                transcript,
                get_display_name(config, cat_slug),
                response,
            )
            dispatch_cat_message(config, cat_slug, response, delay)

    closing_prompt = format_prompt(
        read_prompt("chair_closing_user.md"),
        transcript=transcript,
    )
    chair_close = generate_turn(llm, "chair-cat", closing_prompt)
    transcript = append_transcript_line(transcript, chair_name, chair_close)
    dispatch_cat_message(config, "chair-cat", chair_close, delay)

    return transcript


def run_post_review_async(
    config: dict[str, Any],
    llm: LLMClient,
    summary: str,
    transcript: str,
) -> None:
    def _worker() -> None:
        try:
            review_path = post_council_review(config, llm, summary, transcript)
            if review_path:
                logger.info("Post-council review saved to %s", review_path)
        except Exception:
            logger.exception("Post-council review failed.")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def run_council_pipeline(raw_logs: str, config: dict[str, Any]) -> dict[str, Any]:
    ensure_research_dirs()
    llm = LLMClient.from_config(config)

    logger.info("Starting pre-council analysis.")
    brief = pre_council_analysis(config, llm, raw_logs)
    logger.info("Brief source: %s", brief.source)

    logger.info("Starting council debate loop.")
    transcript = run_debate_loop(config, llm, brief)

    transcript_path = save_transcript(transcript, brief.summary, brief.source)
    logger.info("Transcript saved to %s", transcript_path)

    run_post_review_async(config, llm, brief.summary, transcript)

    return {
        "status": "ok",
        "brief_source": brief.source,
        "transcript_path": str(transcript_path),
    }
