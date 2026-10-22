# Project Specification: "Council of Cats" Agentic Simulation
# Target Environment: Python 3.11+, Linux VPS (single host)

## 1. Executive Summary
A low-stakes agentic AI project designed to run a weekly automated simulation of a "Council of Cats." The system fetches the past 7 days of message history from a specific Discord channel, compiles a bulleted summary, and orchestrates a highly visible, sequential round-robin debate between multiple distinct cat personas using an OpenAI-compatible LLM API (default: **Gemini**). Output is formatted and pushed sequentially to Discord via webhooks.

Pre-council analysis (with cross-week memory) and post-council persona refinement are plain LLM calls through the same client as the debate. The whole pipeline is a hardcoded, deterministic orchestrator — no agent framework is involved.

A split deployment (local Windows PC + Ollama + tunnel) remains possible via legacy `ollama` config for future migration.

---

## 2. Infrastructure Architecture

Everything runs on a **single Linux VPS**:

1. **Council Service (`council_service.py`):**
   - Discord bot running 24/7 via `discord.py`.
   - Listens for `!council run` (admins anytime; others after cooldown).
   - Fetches and sanitizes the past 7 days of channel logs.
   - Calls `council_orchestrator.run_council_pipeline()` in-process (no HTTP tunnel).

2. **Council Orchestrator (`council_orchestrator.py`):**
   - Pre-council analysis (chronicler prompt, with plain-summary fallback).
   - Deterministic 8-turn debate loop via `LLMClient` (Gemini by default).
   - Post-council review (cat self-reviews + curator, async).
   - Transcript and research artifact storage.

Other agents (e.g. Hermes, OpenClaw) may coexist on the VPS if they use separate Discord bots/channels. The council pipeline must not be routed through another agent's gateway.

---

## 3. Core Capabilities & Step-by-Step Logic

### Step 1: Log Scraper (Discord — `council_service.py`)
- Connects to Discord using `discord.py` (v2.0+).
- Triggered manually via `!council run` (admins anytime; other users after `manual_cooldown_days`).
- Grabs the past 7 days of messages in the target channel.
- **Sanitisation:** Strips system messages, bot messages, and empty content; formats as `[Username]: Message content`.
- Passes `raw_logs` directly to the orchestrator.

### Step 2: Pre-Council Analysis (Chronicler)
A single LLM call with the chronicler prompt receives raw logs and produces the council briefing.

- **Prompts:** `prompts/chronicler_system.md`, `prompts/chronicler_user.md`
- **Input:** Raw logs + `research/memory/council_memory.md`
- **Output:** JSON with `summary`, `memory_callbacks`, `memory_updates`
- **Fallback:** Single-shot LLM summarisation (same shape, no memory) if the chronicler call fails or returns invalid JSON.

### Step 3: The Council Debate Loop
Hardcoded deterministic orchestrator. Cat system prompts loaded from `personas/{cat}/`.

- **Main session:** Chair Cat introduction → round-robin debate (2 rounds) → …
- **Extra agenda (optional):** up to `submissions_per_run` FIFO items from `research/submissions.json`. Per item: Chair introduces topic → one cat round → Chair gavel (`CONTINUE: yes/no`) → optional second cat round (capped by `max_extra_rounds`).
- **Close:** Chair Cat closing rulings over the full transcript.
- Discussed submissions are removed only after a successful pipeline run; newer queue entries wait for a later council.

Debate order is configured in `config.json` → `cats.debate_order`. Community items arrive via Discord `!council submit …`.

### Step 4: Post-Council Review (Curator)
Runs async after the debate.

- Each cat self-reviews its own lines (one LLM call per cat, in persona)
- The curator reviews the transcript plus those self-reviews
- `personas/*/council-voice.md` patched automatically
- Proposed `SOUL.md` changes written to `research/persona-patches/` (never auto-overwritten)
- If the curator fails, the self-reviews alone are saved and applied

---

## 4. Persona File Layout

```text
personas/
├── barnaby/   (SOUL.md + council-voice.md)
├── cleo/
├── kiwi/
└── chair-cat/
```

`personas/` is the only copy; review updates are written there directly.

---

## 5. Technical Constraints & API Requirements

### LLM API Integration
- OpenAI-compatible chat completions via `openai` Python SDK.
- Default: Gemini (`https://generativelanguage.googleapis.com/v1beta/openai/`).
- Legacy Ollama supported via `ollama` config block (for local inference migration).
- Standard messages only: `[{"role": "system", ...}, {"role": "user", ...}]`.

### Discord Webhook Dispatcher
- One webhook per cat slug in `config.json` → `webhooks`.
- Sequential delivery with configurable delay (`council.webhook_delay_seconds`).

---

## 6. Directory Blueprint

```text
council-of-cats/
├── config.example.json
├── council_common.py
├── council_orchestrator.py
├── council_service.py           # VPS entry point
├── council_research.py         # Chronicler + post-council review
├── personas/
├── prompts/
├── research/
└── scripts/
    └── run_council_test.py
```

## 7. Configuration Reference

```json
{
  "llm": {
    "provider": "gemini",
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "api_key": "YOUR_GEMINI_API_KEY",
    "model": "gemini-2.5-flash"
  },
  "cats": {
    "debate_order": ["barnaby", "cleo", "kiwi"]
  },
  "council": {
    "log_days": 7,
    "webhook_delay_seconds": 2
  }
}
```

API key may alternatively be supplied via the `LLM_API_KEY` environment variable.
