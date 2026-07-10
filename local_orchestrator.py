"""Local orchestrator: Flask receiver, Ollama debate loop, Hermes sidecar."""

from __future__ import annotations

import logging
import threading
from typing import Any

from flask import Flask, jsonify, request

from council_common import (
    CAT_DISPLAY_NAMES,
    DEBATE_CATS,
    CouncilBrief,
    OllamaClient,
    append_transcript_line,
    dispatch_cat_message,
    ensure_research_dirs,
    format_prompt,
    load_cat_system_prompt,
    load_config,
    read_prompt,
    save_transcript,
)
from hermes_research import post_council_review, pre_council_analysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def generate_turn(
    ollama: OllamaClient,
    cat_slug: str,
    user_prompt: str,
) -> str:
    system_prompt = load_cat_system_prompt(cat_slug)
    return ollama.chat(system_prompt, user_prompt)


def run_debate_loop(
    config: dict[str, Any],
    ollama: OllamaClient,
    brief: CouncilBrief,
) -> str:
    webhooks = config["webhooks"]
    delay = float(config["orchestrator"].get("webhook_delay_seconds", 2))
    transcript = ""

    intro_prompt = format_prompt(
        read_prompt("chair_intro_user.md"),
        summary=brief.summary,
        memory_callbacks=brief.memory_callbacks,
    )
    chair_intro = generate_turn(ollama, "chair-cat", intro_prompt)
    transcript = append_transcript_line(
        transcript, CAT_DISPLAY_NAMES["chair-cat"], chair_intro
    )
    dispatch_cat_message(webhooks, "chair-cat", chair_intro, delay)

    for _round in range(2):
        for cat_slug in DEBATE_CATS:
            turn_prompt = format_prompt(
                read_prompt("debate_turn_user.md"),
                transcript=transcript,
                display_name=CAT_DISPLAY_NAMES[cat_slug],
            )
            response = generate_turn(ollama, cat_slug, turn_prompt)
            transcript = append_transcript_line(
                transcript, CAT_DISPLAY_NAMES[cat_slug], response
            )
            dispatch_cat_message(webhooks, cat_slug, response, delay)

    closing_prompt = format_prompt(
        read_prompt("chair_closing_user.md"),
        transcript=transcript,
    )
    chair_close = generate_turn(ollama, "chair-cat", closing_prompt)
    transcript = append_transcript_line(
        transcript, CAT_DISPLAY_NAMES["chair-cat"], chair_close
    )
    dispatch_cat_message(webhooks, "chair-cat", chair_close, delay)

    return transcript


def run_post_review_async(
    config: dict[str, Any],
    ollama: OllamaClient,
    summary: str,
    transcript: str,
) -> None:
    def _worker() -> None:
        try:
            review_path = post_council_review(config, ollama, summary, transcript)
            if review_path:
                logger.info("Post-council review saved to %s", review_path)
        except Exception:
            logger.exception("Post-council review failed.")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def run_council_pipeline(raw_logs: str, config: dict[str, Any]) -> dict[str, Any]:
    ensure_research_dirs()
    ollama = OllamaClient(
        base_url=config["ollama"]["base_url"],
        model=config["ollama"]["model"],
    )

    logger.info("Starting pre-council analysis.")
    brief = pre_council_analysis(config, ollama, raw_logs)
    logger.info("Brief source: %s", brief.source)

    logger.info("Starting council debate loop.")
    transcript = run_debate_loop(config, ollama, brief)

    transcript_path = save_transcript(transcript, brief.summary, brief.source)
    logger.info("Transcript saved to %s", transcript_path)

    run_post_review_async(config, ollama, brief.summary, transcript)

    return {
        "status": "ok",
        "brief_source": brief.source,
        "transcript_path": str(transcript_path),
    }


@app.post("/council")
def council_endpoint() -> tuple[Any, int]:
    config = load_config()
    payload = request.get_json(silent=True) or {}

    if payload.get("secret") != config["shared_secret"]:
        return jsonify({"error": "unauthorized"}), 401

    raw_logs = payload.get("raw_logs", "").strip()
    if not raw_logs:
        return jsonify({"error": "raw_logs is required"}), 400

    try:
        result = run_council_pipeline(raw_logs, config)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("Council pipeline failed.")
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health() -> tuple[Any, int]:
    return jsonify({"status": "ok"}), 200


def main() -> None:
    config = load_config()
    host = config["orchestrator"]["host"]
    port = int(config["orchestrator"]["port"])
    logger.info("Starting local orchestrator on %s:%s", host, port)
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
