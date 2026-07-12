# Council of Cats

A weekly Discord simulation where cat personas debate your community's drama. Everything runs on a **single VPS**: a Discord bot scrapes channel logs, Gemini generates the council debate, and an optional [Hermes Agent](https://hermes-agent.nousresearch.com/) layer handles pre-council analysis and post-council persona refinement.

Created as a stupid experiment to make use of Hermes agent for research while fullfilling a particular request to "unleash a council of cats upon the Discord". 

## Architecture

```text
Discord  -->  council_service.py  -->  Gemini API
                      |
                      +--> Hermes (chronicler + curator + cat profiles)
                      |
                      +--> Discord webhooks (one per cat)
```

Production debate turn-taking is **deterministic** in `council_orchestrator.py`. Hermes is a research sidecar, not the live orchestrator.

A split deployment (local Windows PC + Ollama + tunnel) is still supported via legacy `ollama` config — see [Migrating to local inference](#migrating-to-local-inference).

## Repository layout

```text
council-of-cats/
├── config.example.json
├── council_common.py          # Shared config, LLM client, personas, webhooks
├── council_orchestrator.py    # Debate pipeline (pre-analysis → turns → post-review)
├── council_service.py         # Discord bot entry point (run this on the VPS)
├── hermes_research.py
├── personas/                  # SOUL.md + council-voice.md per cat
├── prompts/
├── research/
└── scripts/
    ├── seed_hermes_profiles.py
    └── run_council_test.py    # Manual test without Discord
```

## Prerequisites

### VPS (single host)
- Python 3.11+
- Linux (Ubuntu/Debian recommended)
- Discord bot token with **MESSAGE CONTENT INTENT** enabled
- [Gemini API key](https://aistudio.google.com/apikey)
- Optional: [Hermes Agent](https://hermes-agent.nousresearch.com/) on the same VPS

### Discord
- One source channel (logs are read from here)
- Four webhook URLs (Chair Cat, Barnaby, Cleo, Kiwi)
- Bot invited to the server with read/send permissions

Hermes and OpenClaw can run on the same VPS if they use **separate Discord bots/channels** — do not route the council through another agent's gateway.

---

## 1. VPS setup

Automated bootstrap (recommended):

```bash
git clone <your-repo> council-of-cats
cd council-of-cats
./scripts/vps_setup.sh --install-systemd --user "$USER"
# Optional Hermes:
./scripts/vps_setup.sh --install-hermes
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
| `DISCORD_CHANNEL_ID` | Source channel for log scraping |
| `DISCORD_ADMIN_USER_IDS` | Comma-separated user IDs allowed to run `!council run` |
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
| `hermes.enabled` | Set `false` to skip Hermes (LLM fallback for summary) |

Trigger from Discord:

```text
!council run
```

---

## 2. Hermes setup (optional research layer)

Install Hermes on the VPS:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup --portal
```

Point Hermes at Gemini in its own config, then seed council profiles:

```bash
python scripts/seed_hermes_profiles.py
```

| Profile | Role |
|---|---|
| `council-chronicler` | Pre-council analysis + cross-week memory |
| `council-curator` | Post-council review orchestrator |
| `barnaby`, `cleo`, `kiwi`, `chair-cat` | Persona storage and self-review |

Recommended:
- Minimal toolsets for cat profiles (`hermes.toolsets: ""` in config).
- Pin mature skills with `hermes curator pin council-voice`.
- Use a different model for the Curator auxiliary LLM if possible.

### Hermes runtime behaviour

| Step | Hermes profile | Fallback |
|---|---|---|
| Pre-council analysis | `council-chronicler` | Gemini single-shot summary |
| Live debate | *(none — hardcoded loop)* | — |
| Post-council review | `council-curator` + cat profiles | Per-cat self-reviews only |

---

## 3. Pipeline walkthrough

1. **Discord** — `!council run` fetches 7 days of messages, sanitizes to `[Username]: message`.
2. **Pre-council** — chronicler returns JSON: summary, memory callbacks, memory updates.
3. **Debate** — 8 deterministic turns via Gemini:
   - Chair Cat intro
   - Barnaby → Cleo → Kiwi (×2 rounds)
   - Chair Cat closing rulings
4. **Discord** — each turn posts to that cat's webhook with a 2-second delay.
5. **Post-council** — curator reviews transcript, patches `council-voice.md`, writes optional `SOUL.md` candidates to `research/persona-patches/`.

Artifacts:
- `research/transcripts/` — weekly transcripts
- `research/reviews/` — JSON review reports
- `research/persona-patches/` — proposed SOUL changes for human review
- `research/memory/council_memory.md` — cross-week memory
- `personas/*/council-voice.md` — evolving voice guidance

---

## 4. Scheduled runs (cron / systemd — no `!council run` needed)

The Discord bot is only needed for **manual** triggers. For weekly automation, use `scripts/run_council_scheduled.py` — it connects briefly, scrapes logs from `DISCORD_CHANNEL_ID`, runs the pipeline, and posts via webhooks. **No message is posted to the source channel.**

```bash
cd /root/CouncilOfCats
set -a && source council.env && set +a
.venv/bin/python scripts/run_council_scheduled.py
```

Override the source channel for one run:

```bash
.venv/bin/python scripts/run_council_scheduled.py --channel-id 123456789012345678
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

Cat dialogue still goes to the **webhook channels** configured in `council.env`, not to the scrape source channel.

---

## 5. Manual test (no Discord)

```bash
python scripts/run_council_test.py
python scripts/run_council_test.py --logs-file sample_logs.txt
```

This runs the full pipeline including webhooks — use a test Discord server or temporarily disable webhooks if you only want transcript output.

---

## 6. systemd service

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

## 7. Migrating to local inference

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

## 8. Prompt reference

| File | Used by |
|---|---|
| `prompts/chronicler_system.md` | Hermes chronicler / seeded SOUL |
| `prompts/chronicler_user.md` | Pre-council user prompt |
| `prompts/summarize_fallback_*.md` | LLM fallback summary |
| `prompts/chair_intro_user.md` | Chair Cat opening turn |
| `prompts/debate_turn_user.md` | Council member turns |
| `prompts/chair_closing_user.md` | Chair Cat closing turn |
| `prompts/curator_system.md` | Hermes curator / seeded SOUL |
| `prompts/curator_user.md` | Post-council review prompt |
| `prompts/cat_self_review.md` | Per-cat Hermes self-review |
| `personas/*/SOUL.md` | Stable cat identity |
| `personas/*/council-voice.md` | Evolving voice refinements |

---

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| `LLM API key missing` | Set `llm.api_key` or `LLM_API_KEY` env var |
| Gemini auth errors | API key valid? Billing enabled on Google AI Studio? |
| Hermes skipped | `hermes.enabled: false` or `hermes` not on PATH |
| Empty council output | Model name correct? Check service logs |
| Webhooks fail | URLs valid? Messages under Discord 2000-char limit? |
| Unauthorized trigger | Your user ID in `admin_user_ids`? |

---

## 10. Security notes

- Never commit `config.json` or `council.env` (tokens, webhooks, API keys).
- Prefer `council.env` + `LLM_API_KEY` over hardcoding secrets in `config.json`.
- Restrict `!council run` to `admin_user_ids`.
- Review `research/persona-patches/` before applying any `SOUL.md` changes.
- Version-control `personas/` and inspect `git diff` after weekly reviews.
