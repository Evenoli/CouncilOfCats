# Project Specification: "Council of Cats" Agentic Simulation
# Target Environment: Python 3.11+, Windows (Local Inference) + Linux VPS (Discord Gateway)

## 1. Executive Summary
A low-stakes agentic AI project designed to run a weekly automated simulation of a "Council of Cats." The system fetches the past 7 days of message history from a specific Discord channel, compiles a bulleted summary, and orchestrates a highly visible, sequential round-robin debate between multiple distinct cat personas using a local LLM via Ollama. Output is formatted and pushed sequentially to a Discord channel via Webhooks.

A **Hermes Agent research layer** runs alongside the production pipeline. Hermes handles pre-council analysis (with cross-week memory) and post-council persona refinement. The live debate loop remains a hardcoded, deterministic orchestrator — Hermes does not orchestrate turn-taking or webhook delivery.

---

## 2. Infrastructure Architecture
To optimize cost (zero token fees) and handle continuous Discord connections, the codebase is split into two operational modes:

1. **VPS Component (Gateway):** 
   - Lightweight Python script running 24/7 on a Linux VPS.
   - Listens for a specific Discord trigger (e.g., `!council run`).
   - Fetches the past 7 days of channel message logs and sanitizes them.
   - Pushes the raw text payload via an HTTP POST request down a secure tunnel (Ngrok/Cloudflare) to the local machine.

2. **Local Machine Component (Orchestrator & Brains):**
   - Python script running on a powerful Windows PC (64GB RAM, RTX 5070 Ti).
   - Listens for payloads on a local Flask endpoint (e.g., port 5000).
   - Orchestrates the sequential AI agent loop by making API requests to a local Ollama instance (`http://localhost:11434/v1`).
   - Models used: Qwen-2.5-32B-Instruct or Qwen-2.5-14B-Instruct running at 64k context window.
   - Iterates through cat turns, records the transcript, and sends distinct payloads to Discord webhooks.
   - Reads cat persona prompts from the synced `personas/` directory (see Section 5).

3. **Hermes Research Layer (Local Machine, optional but intended):**
   - [Hermes Agent](https://hermes-agent.nousresearch.com/) installed on the same Windows PC.
   - Runs as a sidecar to the production pipeline — not a replacement for `local_orchestrator.py`.
   - Uses isolated **profiles** for the chronicler, curator, and each cat persona.
   - Persona files are synced from Hermes profile directories into `personas/` after each review cycle.

---

## 3. Core Capabilities & Step-by-Step Logic

### Step 1: Log Scraper (VPS Side)
- Connects to Discord using `discord.py` (v2.0+).
- Triggered manually by an admin via `!council run`.
- Grabs the past 7 days of messages in the target channel.
- **Sanitisation:** Strips out system messages, bot messages, reactions, embedded images, and standardizes formats to `[Username]: Message content`.
- Posts JSON to Local PC: `{"secret": "YOUR_KEY", "raw_logs": "..."}`

### Step 2: Pre-Council Analysis (Local Machine — Hermes)
The `council-chronicler` Hermes profile receives the raw sanitized logs and produces the briefing for the council.

- **Profile:** `council-chronicler` (isolated Hermes profile with cross-week `MEMORY.md`).
- **Input:** Raw sanitized logs + prior weekly memory (running gags, past rulings, recurring themes).
- **Output:**
  - A precise **3-4 bullet-point summary** highlighting the biggest drama, jokes, or events of the week.
  - **Memory callbacks** for Chair Cat's opening (e.g. references to prior rulings or running jokes).
  - **Memory updates** to persist for future weeks (new gags, notable events, rulings).
- **Fallback:** If Hermes is unavailable, `local_orchestrator.py` falls back to a single-shot Ollama summarisation prompt (same output shape, no memory).

### Step 3: The Council Debate Loop (Local Machine)
The script acts as a hardcoded, deterministic orchestrator to ensure strict turn-taking. It maintains a running text string named `council_transcript`. Cat system prompts are loaded from `personas/{cat}/` (see Section 5), not hardcoded in Python.

- **Turn 1: The Chair Cat (Introduction)**
  - System Prompt: loaded from `personas/chair-cat/SOUL.md` + `personas/chair-cat/council-voice.md`.
  - User Prompt: `Review these weekly human events: {summary}. {memory_callbacks} Introduce the issues dramatically to the High Council and command them to begin debating.`
  - Output is captured, added to `council_transcript`, and pushed to the Chair Cat's Discord Webhook.

- **Turns 2-7: Round-Robin Council Debate (2 rounds per cat)**
  - The script iterates through an array of defined personas (exactly 2 complete rotations):
    - **Barnaby (The Cynic)**
    - **Cleo (The Intellectual)**
    - **Pip (The Chaos Agent)**
  - **Initial persona seeds** (written into each profile's `SOUL.md` on first setup):
    - **Barnaby:** `You are Barnaby, an ancient, cynical Persian cat. You speak in a crotchety voice, hate change, and find human internet actions trivial and annoying.`
    - **Cleo:** `You are Cleo, a regal Egyptian Mau who believes cats are gods. You speak in grand, elevated prose and analyze human actions through historical/philosophical lenses.`
    - **Pip:** `You are Pip, a hyperactive ginger kitten with no attention span. Speak in short, frantic fragments, use caps lock for excitement, and get easily distracted by lasers.`
  - **Prompting Mechanism:** For every turn, the current cat is passed the entire updated `council_transcript` and explicitly instructed: `It is your turn, [Name]. Read the transcript above and respond naturally in character. Limit your response to 2-3 sentences.`
  - After each generation, the response is appended to `council_transcript` and immediately posted to that cat's specific Discord Webhook.

- **Turn 8: The Chair Cat (Rulings & Conclusion)**
  - System Prompt: loaded from `personas/chair-cat/SOUL.md` + `personas/chair-cat/council-voice.md`.
  - User Prompt: `The debate has ended. Review the full transcript: {council_transcript}. Issue your final, ridiculous feline rulings on the human actions, hit your gavel, and close the meeting.`
  - Appended to transcript and sent to the Chair Cat Webhook.

### Step 4: Post-Council Review (Local Machine — Hermes)
After the production debate completes, the `council-curator` Hermes profile reviews the transcript and refines cat personas.

- **Profile:** `council-curator` (orchestrates review; does not rewrite personas directly).
- **Input:** `{summary}`, `{council_transcript}`, per-cat line extractions.
- **Process:**
  1. Curator delegates to each cat profile in parallel via `delegate_task` (profile pinned):
     - `barnaby`, `cleo`, `pip`, `chair-cat`
  2. Each cat subagent reviews only its own lines and returns:
     - In-character consistency score (1–5)
     - Length compliance
     - 1–2 specific voice improvements
     - A proposed patch to its `council-voice` skill (not `SOUL.md`)
  3. Curator consolidates patches and writes updated `council-voice` skills.
  4. Curator writes any proposed `SOUL.md` changes to `research/persona-patches/` for optional human review (Hermes never auto-overwrites `SOUL.md`).
  5. Sync updated persona files from Hermes profiles into `personas/` (see Section 5).
- **Output:** Updated persona skills, git-tracked patch files, review report in `research/reviews/`.
- **Guardrails:**
  - `SOUL.md` = stable core identity (archetype, name, fundamental voice rules). Changed rarely and only via explicit approval or small auto-applied diffs.
  - `council-voice` skill = evolving refinements (catchphrases, anti-patterns, weekly callbacks). Patched automatically by the Hermes Curator.
  - Pin stable skills with `hermes curator pin` once mature.
  - Version-control the `personas/` directory; review `git diff` after each weekly run.

---

## 4. Hermes Profile Setup

Create the following isolated Hermes profiles (each with its own `SOUL.md`, memory, and skills):

| Profile | Role |
|---|---|
| `council-chronicler` | Pre-council analysis, cross-week memory, weekly brief generation |
| `council-curator` | Post-council review orchestrator; delegates to cat profiles |
| `barnaby` | Barnaby persona storage and self-review |
| `cleo` | Cleo persona storage and self-review |
| `pip` | Pip persona storage and self-review |
| `chair-cat` | Chair Cat persona storage and self-review |

```bash
hermes profile create council-chronicler
hermes profile create council-curator
hermes profile create barnaby
hermes profile create cleo
hermes profile create pip
hermes profile create chair-cat
```

Hermes is **not** used for live debate orchestration, webhook delivery, or turn order. Those remain the responsibility of `local_orchestrator.py`.

---

## 5. Persona File Layout & Sync

Cat personas are stored in two places: Hermes profiles (source of truth for evolution) and the project `personas/` directory (source of truth for production prompts).

```text
personas/
├── barnaby/
│   ├── SOUL.md              # Stable core identity
│   └── council-voice.md     # Evolving voice refinements (synced from Hermes skill)
├── cleo/
│   ├── SOUL.md
│   └── council-voice.md
├── pip/
│   ├── SOUL.md
│   └── council-voice.md
└── chair-cat/
    ├── SOUL.md
    └── council-voice.md
```

**Sync direction (after post-council review):**
```text
~/.hermes/profiles/{cat}/SOUL.md                    →  personas/{cat}/SOUL.md
~/.hermes/profiles/{cat}/skills/council-voice/      →  personas/{cat}/council-voice.md
```

`local_orchestrator.py` loads prompts via:
```python
def load_cat_system_prompt(cat_name: str) -> str:
    base = read_file(f"personas/{cat_name}/SOUL.md")
    voice = read_file(f"personas/{cat_name}/council-voice.md")
    return f"{base}\n\n{voice}"
```

---

## 6. Technical Constraints & API Requirements

### Ollama API Integration
- Use the official `openai` Python SDK or basic `requests` payloads pointing to `http://localhost:11434/v1/chat/completions`.
- **CRITICAL:** Do NOT pass `thinking` or `reasoning_effort` configurations, as standard Ollama models will reject the payload with a 400 error.
- All requests must include standard chat arrays: `[{"role": "system", "content": "..." }, {"role": "user", "content": "..."}]`.

### Discord Webhook Dispatcher
- Maintain a dictionary mapping Cat Names to unique Webhook URLs:
  ```python
  WEBHOOKS = {
      "Chair Cat": "URL_1",
      "Barnaby": "URL_2",
      "Cleo": "URL_3",
      "Pip": "URL_4"
  }
  ```
- Deliver messages sequentially with a brief built-in delay (e.g., `time.sleep(2)`) between webhooks to maintain reading pacing in Discord.

### Hermes Integration
- Hermes steps (2 and 4) are optional at runtime but are the intended research path.
- Cat profiles used during review should have minimal toolsets (no web browsing, terminal, etc.).
- Use a different model/provider for the Curator auxiliary LLM than for cat review subagents where possible, to reduce self-confirming bias.
- Proposed `SOUL.md` patches land in `research/persona-patches/`; applied `SOUL.md` changes should be git-committed.

---

## 7. Directory Blueprint (Suggested for Generation)
```text
council-of-cats/
├── config.example.json          # Template for Discord Tokens, Webhooks, and Secret Keys
├── vps_gateway.py               # Runs on VPS: Discord Bot listener & message aggregator
├── local_orchestrator.py        # Runs on Windows: Flask receiver, Ollama debate loop
├── hermes_research.py           # Hermes integration: pre-council analysis, post-council review, persona sync
├── personas/                    # Synced cat persona files (SOUL.md + council-voice.md per cat)
│   ├── barnaby/
│   ├── cleo/
│   ├── pip/
│   └── chair-cat/
└── research/                    # Review outputs, persona patches, weekly transcripts
    ├── reviews/
    ├── persona-patches/
    └── transcripts/
```

## 8. Prompt for Cursor Integration
> "Generate the codebase outlined in `project_spec.md`. Ensure that `local_orchestrator.py` uses standard OpenAI-compatible API calls for Ollama, loads cat system prompts dynamically from `personas/{cat}/`, and appends outputs cleanly to a running transcript file in `research/transcripts/`. Implement `hermes_research.py` as an optional sidecar for pre-council analysis (Step 2) and post-council review (Step 4), with a fallback to simple Ollama summarisation when Hermes is unavailable. Provide `config.example.json` and seed `personas/` with the initial cat SOUL.md content from the spec."
