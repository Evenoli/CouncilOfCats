"""Council debate pipeline: pre-analysis, turn loop, post-review."""

from __future__ import annotations

import logging
import threading
from typing import Any

from council_common import (
    CouncilBrief,
    LLMClient,
    append_transcript_line,
    clear_submissions_by_ids,
    dispatch_cat_message,
    ensure_research_dirs,
    format_prompt,
    get_debate_cats,
    get_display_name,
    get_max_extra_rounds,
    get_submissions_per_run,
    load_cat_system_prompt,
    load_submissions,
    parse_chair_continue,
    peek_submissions_for_run,
    read_prompt,
    record_council_run,
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


def _run_cat_round(
    config: dict[str, Any],
    llm: LLMClient,
    transcript: str,
    delay: float,
) -> str:
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
    return transcript


def run_extra_submissions_phase(
    config: dict[str, Any],
    llm: LLMClient,
    transcript: str,
    submissions: list[dict[str, Any]],
    delay: float,
) -> str:
    """Discuss queued agenda items after the main weekly rounds."""
    if not submissions:
        return transcript

    chair_name = get_display_name(config, "chair-cat")
    max_rounds = get_max_extra_rounds(config)
    total = len(submissions)

    for index, submission in enumerate(submissions, start=1):
        topic = str(submission.get("text", "")).strip()
        submitter = str(submission.get("submitted_by", "unknown")).strip() or "unknown"
        if not topic:
            logger.warning("Skipping empty submission id=%s", submission.get("id"))
            continue

        logger.info(
            "Extra agenda item %s/%s: %s",
            index,
            total,
            topic[:80],
        )
        intro_prompt = format_prompt(
            read_prompt("chair_extra_topic_user.md"),
            index=str(index),
            total=str(total),
            submitter=submitter,
            topic=topic,
            transcript=transcript,
        )
        chair_intro = generate_turn(llm, "chair-cat", intro_prompt)
        transcript = append_transcript_line(transcript, chair_name, chair_intro)
        dispatch_cat_message(config, "chair-cat", chair_intro, delay)

        transcript = _run_cat_round(config, llm, transcript, delay)

        rounds_done = 1
        while rounds_done < max_rounds:
            gavel_prompt = format_prompt(
                read_prompt("chair_extra_gavel_user.md"),
                topic=topic,
                transcript=transcript,
            )
            gavel_raw = generate_turn(llm, "chair-cat", gavel_prompt)
            should_continue, gavel_spoken = parse_chair_continue(gavel_raw)
            transcript = append_transcript_line(transcript, chair_name, gavel_spoken)
            dispatch_cat_message(config, "chair-cat", gavel_spoken, delay)
            if not should_continue:
                logger.info(
                    "Chair closed extra topic %s/%s after %s round(s).",
                    index,
                    total,
                    rounds_done,
                )
                break

            logger.info(
                "Chair continued extra topic %s/%s into round %s.",
                index,
                total,
                rounds_done + 1,
            )
            transcript = _run_cat_round(config, llm, transcript, delay)
            rounds_done += 1

    return transcript


def run_debate_loop(
    config: dict[str, Any],
    llm: LLMClient,
    brief: CouncilBrief,
    submissions: list[dict[str, Any]] | None = None,
) -> str:
    delay = float(config.get("council", {}).get("webhook_delay_seconds", 2))
    transcript = ""
    pending = submissions if submissions is not None else []

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
        transcript = _run_cat_round(config, llm, transcript, delay)

    transcript = run_extra_submissions_phase(
        config,
        llm,
        transcript,
        pending,
        delay,
    )

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

    submissions = peek_submissions_for_run(get_submissions_per_run(config))
    submission_ids = [str(item.get("id", "")) for item in submissions if item.get("id")]
    if submissions:
        logger.info("Loaded %s queued submission(s) for this run.", len(submissions))
    else:
        logger.info("No queued submissions for this run.")

    logger.info("Starting pre-council analysis.")
    brief = pre_council_analysis(config, llm, raw_logs)
    logger.info("Brief source: %s", brief.source)

    logger.info("Starting council debate loop.")
    transcript = run_debate_loop(config, llm, brief, submissions=submissions)

    transcript_path = save_transcript(transcript, brief.summary, brief.source)
    record_council_run()
    cleared = clear_submissions_by_ids(submission_ids)
    if cleared:
        logger.info("Cleared %s discussed submission(s) from the queue.", cleared)
    logger.info("Transcript saved to %s", transcript_path)

    run_post_review_async(config, llm, brief.summary, transcript)

    return {
        "status": "ok",
        "brief_source": brief.source,
        "transcript_path": str(transcript_path),
        "submissions_discussed": cleared,
        "submissions_remaining": len(load_submissions()),
    }
