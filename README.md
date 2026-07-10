# Council of Cats

A weekly Discord simulation where cat personas debate your community's drama. A VPS bot scrapes channel logs, a local Windows orchestrator runs the council via Ollama, and an optional [Hermes Agent](https://hermes-agent.nousresearch.com/) layer handles pre-council analysis and post-council persona refinement.

## Architecture

```text
Discord (VPS)  --tunnel-->  Local Flask orchestrator  -->  Ollama
                                |
                                +--> Hermes (chronicler + curator + cat profiles)
                                |
                                +--> Discord webhooks (one per cat)
```

Production debate turn-taking is **deterministic** in `local_orchestrator.py`. Hermes is a research sidecar, not the live orchestrator.

## Repository layout

```text
council-of-cats/
├── config.example.json
├── council_common.py
├── vps_gateway.py
├── local_orchestrator.py
├── hermes_research.py
├── personas/                 # SOUL.md + council-voice.md per cat
├── prompts/                  # Base prompts for all pipeline stages
├── research/                 # Transcripts, reviews, memory, persona patches
└── scripts/
    └── seed_hermes_profiles.py
```

## Prerequisites

### Local Windows PC (orchestrator)
- Python 3.11+
- [Ollama](https://ollama.com/) with a chat model pulled, e.g.:
  ```powershell
  ollama pull qwen2.5:32b-instruct
  ```
- Optional but intended: [Hermes Agent](https://hermes-agent.nousresearch.com/)
- A tunnel exposing your local Flask port (Cloudflare Tunnel or ngrok)

### Linux VPS (gateway)
- Python 3.11+
- Discord bot token with `MESSAGE CONTENT INTENT` enabled
- Outbound HTTPS to your tunnel URL

### Discord
- One source channel (logs are read from here)
- Four webhook URLs (Chair Cat, Barnaby, Cleo, Pip)
- Bot invited to the server with read/send permissions

---

## 1. Initial setup (local machine)

```powershell
cd "path\to\CouncilOfCats"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.json config.json
```

Edit `config.json`:

| Key | Purpose |
|---|---|
| `shared_secret` | Long random string shared with the VPS |
| `ollama.base_url` | Usually `http://localhost:11434/v1` |
| `ollama.model` | Model tag, e.g. `qwen2.5:32b-instruct` |
| `webhooks.*` | Discord webhook URL per cat slug |
| `orchestrator.host` / `port` | Flask bind address (default `0.0.0.0:5000`) |
| `hermes.enabled` | Set `false` to skip Hermes and use Ollama fallback only |

Verify Ollama is running:

```powershell
curl http://localhost:11434/api/tags
```

Start the orchestrator:

```powershell
python local_orchestrator.py
```

Health check:

```powershell
curl http://localhost:5000/health
```

---

## 2. Expose the local orchestrator (tunnel)

The VPS must reach `POST /council` on your PC. Example with Cloudflare Tunnel:

```powershell
cloudflared tunnel --url http://localhost:5000
```

Copy the public HTTPS URL into `config.json` on **both** machines as `local_orchestrator_url`, e.g.:

```json
"local_orchestrator_url": "https://abc123.trycloudflare.com/council"
```

Keep the tunnel and `local_orchestrator.py` running during council sessions.

---

## 3. VPS gateway setup

On the VPS:

```bash
git clone <your-repo> council-of-cats
cd council-of-cats
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json` on the VPS with at least:

```json
{
  "shared_secret": "SAME_AS_LOCAL",
  "local_orchestrator_url": "https://your-tunnel.example.com/council",
  "discord": {
    "bot_token": "YOUR_BOT_TOKEN",
    "channel_id": "SOURCE_CHANNEL_ID",
    "admin_user_ids": ["YOUR_USER_ID"]
  }
}
```

Run the gateway (use systemd, pm2, or screen for persistence):

```bash
python vps_gateway.py
```

Trigger a council run from Discord:

```text
!council run
```

Only users listed in `admin_user_ids` can trigger a run.

---

## 4. Hermes setup (optional research layer)

Install Hermes on the local Windows machine:

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
hermes setup --portal
```

Create profiles and seed persona files:

```powershell
python scripts/seed_hermes_profiles.py
```

This creates six profiles:

| Profile | Role |
|---|---|
| `council-chronicler` | Pre-council analysis + cross-week memory |
| `council-curator` | Post-council review orchestrator |
| `barnaby`, `cleo`, `pip`, `chair-cat` | Persona storage and self-review |

Recommended Hermes config notes:
- Give cat profiles **minimal toolsets** during review (empty `hermes.toolsets` in `config.json` is fine).
- Use a different model/provider for the Curator auxiliary LLM if possible.
- Pin mature skills later with `hermes curator pin council-voice`.

### Hermes runtime behaviour

| Step | Hermes profile | Fallback |
|---|---|---|
| Pre-council analysis | `council-chronicler` | Ollama single-shot summary |
| Live debate | *(none — hardcoded loop)* | — |
| Post-council review | `council-curator` + cat profiles | Saves per-cat reviews only |

Hermes is invoked via:

```text
hermes -p <profile> -z "<prompt>"
```

After review, persona files sync from `~/.hermes/profiles/<cat>/` into `personas/`.

---

## 5. Pipeline walkthrough

1. **VPS** — `!council run` fetches 7 days of messages, sanitizes to `[Username]: message`, POSTs to `/council`.
2. **Pre-council** — chronicler returns JSON: summary, memory callbacks, memory updates. Memory is appended to `research/memory/council_memory.md`.
3. **Debate** — 8 deterministic turns:
   - Chair Cat intro
   - Barnaby → Cleo → Pip (×2 rounds)
   - Chair Cat closing rulings
4. **Discord** — each turn posts to that cat's webhook with a 2-second delay.
5. **Post-council** — curator reviews transcript, patches `council-voice.md`, writes optional `SOUL.md` candidates to `research/persona-patches/`.

Artifacts land in:
- `research/transcripts/` — full weekly transcripts
- `research/reviews/` — JSON review reports
- `research/persona-patches/` — proposed SOUL changes for human review
- `personas/*/council-voice.md` — evolving voice guidance

---

## 6. Manual local test (no Discord bot)

With `local_orchestrator.py` running:

```powershell
curl -X POST http://localhost:5000/council `
  -H "Content-Type: application/json" `
  -d "{\"secret\":\"YOUR_SECRET\",\"raw_logs\":\"[Alice]: We argued about pizza again\n[Bob]: lasers\"}"
```

---

## 7. systemd examples

### VPS gateway (`/etc/systemd/system/council-gateway.service`)

```ini
[Unit]
Description=Council of Cats Discord Gateway
After=network.target

[Service]
Type=simple
User=council
WorkingDirectory=/opt/council-of-cats
ExecStart=/opt/council-of-cats/.venv/bin/python vps_gateway.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Local orchestrator (Windows Task Scheduler or NSSM)

Run at logon:

```powershell
cd C:\CouncilOfCats
.\.venv\Scripts\python.exe local_orchestrator.py
```

Pair with a persistent Cloudflare tunnel service pointing at port 5000.

---

## 8. Prompt reference

| File | Used by |
|---|---|
| `prompts/chronicler_system.md` | Hermes chronicler / seeded SOUL |
| `prompts/chronicler_user.md` | Pre-council user prompt |
| `prompts/summarize_fallback_*.md` | Ollama fallback summary |
| `prompts/chair_intro_user.md` | Chair Cat opening turn |
| `prompts/debate_turn_user.md` | Barnaby / Cleo / Pip turns |
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
| VPS cannot reach orchestrator | Tunnel running? `local_orchestrator_url` ends with `/council`? |
| `401 unauthorized` | `shared_secret` must match on VPS and local |
| Ollama 400 errors | Do not pass `thinking` / `reasoning_effort` params (handled in code) |
| Hermes skipped | `hermes.enabled: false`, or `hermes` not on PATH — fallback summary still runs |
| Empty council output | Model pulled in Ollama? `ollama.model` matches `ollama list`? |
| Webhooks fail | URLs valid? Messages under Discord 2000-char limit? |

---

## 10. Security notes

- Never commit `config.json` (tokens, webhooks, shared secret).
- Restrict `!council run` to `admin_user_ids`.
- Review `research/persona-patches/` before applying any `SOUL.md` changes.
- Version-control `personas/` and inspect `git diff` after weekly reviews.
