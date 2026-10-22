# Council of Cats

A weekly Discord simulation where cat personas debate your community's drama. Everything runs on a **single VPS**: a Discord bot scrapes channel logs, and Gemini handles pre-council analysis, the council debate, and post-council persona refinement.

Created as a stupid experiment to make use of Hermes agent for research while fullfilling a particular request to "unleash a council of cats upon the Discord". 

## Architecture

```text
Discord  -->  council_service.py  -->  Gemini API
                      |                  (chronicler, debate, self-reviews, curator)
                      |
                      +--> Discord webhooks (one per cat)
```

The whole pipeline is **deterministic** Python in `council_orchestrator.py`; every model call goes through `LLMClient`. (Hermes Agent was used here originally, but only as a tool-less prompt runner, so it has been removed.)

A split deployment (local Windows PC + Ollama + tunnel) is still supported via legacy `ollama` config — see [Migrating to local inference](#migrating-to-local-inference).

## Repository layout

```text
council-of-cats/
├── config.example.json
├── council_common.py          # Shared config, LLM client, personas, webhooks
├── council_orchestrator.py    # Debate pipeline (pre-analysis → turns → post-review)
├── council_service.py         # Discord bot entry point (run this on the VPS)
├── council_research.py        # Chronicler + post-council review
├── personas/                  # SOUL.md + council-voice.md per cat
├── prompts/
├── research/
└── scripts/
    └── run_council_test.py    # Manual test without Discord
```

## Prerequisites

### VPS (single host)
- Python 3.11+
- Linux (Ubuntu/Debian recommended)
- Discord bot token with **MESSAGE CONTENT INTENT** enabled
- [Gemini API key](https://aistudio.google.com/apikey)

### Discord
- One source channel (logs are read from here)
- Four webhook URLs (Chair Cat, Barnaby, Cleo, Kiwi)
- Bot invited to the server with read/send permissions

Other agents (e.g. Hermes, OpenClaw) can run on the same VPS if they use **separate Discord bots/channels** — do not route the council through another agent's gateway.

---

## 1. VPS setup

Automated bootstrap (recommended):

```bash
git clone <your-repo> council-of-cats
cd council-of-cats
./scripts/vps_setup.sh --install-systemd --user "$USER"
```

Then fill in secrets:

```bash
cp deploy/council.env.example council.env   # skipped if vps_setup.sh already created it
chmod 600 council.env
nano council.env
```

`council.env` fields (never commit this file):

| Variable | Purpose |
|---|---|
| `LLM_API_KEY` | Gemini API key |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_READ_CHANNEL_ID` | Channel to scrape user messages from (7-day window) |
| `DISCORD_STATUS_CHANNEL_ID` | Channel for bot completion messages (scheduled runs; also used for manual runs when set) |
| `DISCORD_ADMIN_USER_IDS` | Comma-separated user IDs who may run `!council run` at any time |
| `WEBHOOK_CHAIR_CAT` | Chair Cat webhook URL |
| `WEBHOOK_BARNABY` | Barnaby webhook URL |
| `WEBHOOK_CLEO` | Cleo webhook URL |
| `WEBHOOK_KIWI` | Kiwi webhook URL |

Verify readiness:

```bash
python scripts/preflight_check.py
```

Start the service:

```bash
./scripts/start_council.sh
# or: sudo systemctl enable --now council
```

Manual setup (equivalent):

```bash
git clone <your-repo> council-of-cats
cd council-of-cats
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json` for non-secret settings (or use env vars above for secrets):

| Key | Purpose |
|---|---|
| `llm.model` | e.g. `gemini-2.5-flash` |
| `cats.debate_order` | Round-robin council members |
| `council.manual_cooldown_days` | Days non-admins must wait between manual runs (default `6`) |
| `council.submissions_per_run` | Max queued agenda items discussed per run (default `4`, FIFO) |
| `council.max_extra_rounds` | Max cat rounds per agenda item, including the first (default `2`) |
| `council.max_submission_chars` | Max length for `!council submit` text (default `500`) |

Discord commands:

```text
!council run
!council submit why did the humans buy a second laser pointer
!council submissions
```

Admins in `DISCORD_ADMIN_USER_IDS` / `admin_user_ids` may trigger `!council run` at any time. Anyone else may trigger only if at least `manual_cooldown_days` have passed since the last successful run (manual or scheduled).

Anyone may queue agenda items with `!council submit …`. The queue is uncapped; each successful run discusses up to `submissions_per_run` oldest items, then clears only those. After the first cat round on an item, the Chair gavels `CONTINUE: yes/no` and may open one further round.

---

## 2. Pipeline walkthrough

1. **Discord** — `!council run` fetches 7 days of messages, sanitizes to `[Username]: message`. Loads up to `submissions_per_run` oldest queued agenda items.
2. **Pre-council** — chronicler prompt returns JSON: summary, memory callbacks, memory updates (plain summary fallback if that fails).
3. **Debate** — deterministic turns via Gemini:
   - Chair Cat intro
   - Barnaby → Cleo → Kiwi (×2 rounds)
   - For each queued agenda item (up to 4): Chair introduces topic → one cat round → Chair gavel (`CONTINUE: yes/no`) → optional second cat round
   - Chair Cat closing rulings
4. **Discord** — each turn posts to that cat's webhook with a 2-second delay.
5. **Post-council** — each cat self-reviews its lines, then the curator reviews the transcript plus those self-reviews, patches `council-voice.md`, writes optional `SOUL.md` candidates to `research/persona-patches/`. Discussed submissions are cleared from the queue only after a successful run.

Artifacts:
- `research/transcripts/` — weekly transcripts
- `research/reviews/` — JSON review reports
- `research/persona-patches/` — proposed SOUL changes for human review
- `research/memory/council_memory.md` — cross-week memory
- `research/submissions.json` — FIFO community agenda queue
- `personas/*/council-voice.md` — evolving voice guidance

---

## 3. Scheduled runs (cron / systemd — no `!council run` needed)

The Discord bot is only needed for **manual** triggers. For weekly automation, use `scripts/run_council_scheduled.py` — it connects briefly, scrapes logs from `DISCORD_READ_CHANNEL_ID`, runs the pipeline, posts cat dialogue via webhooks, and posts a completion message to `DISCORD_STATUS_CHANNEL_ID`.

```bash
cd /root/CouncilOfCats
set -a && source council.env && set +a
.venv/bin/python scripts/run_council_scheduled.py
```

**systemd timer** (after `vps_setup.sh`, edit paths in templates first):

```bash
sed -e 's|@INSTALL_DIR@|/root/CouncilOfCats|g' -e 's|@SERVICE_USER@|root|g' \
  deploy/council-scheduled.service.template | sudo tee /etc/systemd/system/council-scheduled.service
sed -e 's|@INSTALL_DIR@|/root/CouncilOfCats|g' \
  deploy/council.timer.template | sudo tee /etc/systemd/system/council.timer
sudo systemctl daemon-reload
sudo systemctl enable --now council.timer
systemctl list-timers council.timer
```

**crontab** (Sundays 18:00 UTC):

```cron
0 18 * * 0 cd /root/CouncilOfCats && set -a && source council.env && set +a && .venv/bin/python scripts/run_council_scheduled.py >> research/cron.log 2>&1
```

Cat dialogue still goes to the **webhook channels** configured in `council.env`. Completion summaries go to the **status channel**.

`DISCORD_CHANNEL_ID` is still accepted as a legacy alias for `DISCORD_READ_CHANNEL_ID`.

---

## 4. Manual test (no Discord)

```bash
python scripts/run_council_test.py
python scripts/run_council_test.py --logs-file sample_logs.txt
```

This runs the full pipeline including webhooks — use a test Discord server or temporarily disable webhooks if you only want transcript output.

---

## 5. systemd service

`/etc/systemd/system/council.service`:

```ini
[Unit]
Description=Council of Cats
After=network.target

[Service]
Type=simple
User=council
WorkingDirectory=/opt/council-of-cats
Environment=LLM_API_KEY=your-gemini-key-here
ExecStart=/opt/council-of-cats/.venv/bin/python council_service.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now council
sudo journalctl -u council -f
```

---

## 6. Migrating to local inference

To run the debate loop on a Windows PC with Ollama again, replace the `llm` block with legacy Ollama config and split the gateway:

```json
"llm": {
  "base_url": "http://localhost:11434/v1",
  "api_key": "ollama",
  "model": "qwen2.5:32b-instruct"
}
```

You would then reintroduce a separate HTTP receiver or run `scripts/run_council_test.py` locally. The pipeline code (`council_orchestrator.py`) is unchanged — only deployment topology differs.

---

## 7. Prompt reference

| File | Used by |
|---|---|
| `prompts/chronicler_system.md` | Pre-council system prompt |
| `prompts/chronicler_user.md` | Pre-council user prompt |
| `prompts/summarize_fallback_*.md` | LLM fallback summary |
| `prompts/chair_intro_user.md` | Chair Cat opening turn |
| `prompts/debate_turn_user.md` | Council member turns |
| `prompts/chair_closing_user.md` | Chair Cat closing turn |
| `prompts/curator_system.md` | Post-council review system prompt |
| `prompts/curator_user.md` | Post-council review prompt |
| `prompts/cat_self_review.md` | Per-cat self-review |
| `personas/*/SOUL.md` | Stable cat identity |
| `personas/*/council-voice.md` | Evolving voice refinements |

---

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `LLM API key missing` | Set `llm.api_key` or `LLM_API_KEY` env var |
| Gemini auth errors | API key valid? Billing enabled on Google AI Studio? |
| `Brief source: llm-fallback` | Chronicler returned invalid JSON — check service logs |
| Empty council output | Model name correct? Check service logs |
| Webhooks fail | URLs valid? Messages under Discord 2000-char limit? |
| Manual trigger denied (cooldown) | Wait for `manual_cooldown_days`, or use an admin user ID |

---

## 9. Security notes

- Never commit `config.json` or `council.env` (tokens, webhooks, API keys).
- Prefer `council.env` + `LLM_API_KEY` over hardcoding secrets in `config.json`.
- Admins may always run `!council run`; others are limited by `manual_cooldown_days`.
- Review `research/persona-patches/` before applying any `SOUL.md` changes.
- Version-control `personas/` and inspect `git diff` after weekly reviews.
