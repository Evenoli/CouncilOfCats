#!/usr/bin/env bash
# Bootstrap Council of Cats on a Linux VPS.
# Secrets go in council.env (see deploy/council.env.example) — not committed to git.
#
# Usage:
#   ./scripts/vps_setup.sh
#   ./scripts/vps_setup.sh --install-hermes
#   ./scripts/vps_setup.sh --install-systemd --user "$USER" --install-dir "$HOME/council-of-cats"

set -euo pipefail

INSTALL_HERMES=false
INSTALL_SYSTEMD=false
SERVICE_USER="${USER:-council}"
INSTALL_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-hermes) INSTALL_HERMES=true; shift ;;
        --install-systemd) INSTALL_SYSTEMD=true; shift ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$PROJECT_ROOT}"
VENV_DIR="$INSTALL_DIR/.venv"

echo "==> Council of Cats VPS setup"
echo "    Project:  $INSTALL_DIR"
echo "    User:     $SERVICE_USER"

# --- System packages (best-effort; may need sudo) ---
if command -v apt-get >/dev/null 2>&1; then
    if ! python3 -c 'import venv' 2>/dev/null; then
        echo "==> Installing python3-venv (may prompt for sudo)..."
        sudo apt-get update -qq
        sudo apt-get install -y python3-venv python3-pip git
    fi
fi

# --- Python version ---
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
if [[ "$PY_MINOR" -lt 11 ]]; then
    echo "ERROR: Python 3.11+ required (found $PY_VERSION)." >&2
    exit 1
fi
echo "==> Python $PY_VERSION OK"

# --- Virtualenv ---
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating virtualenv..."
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$INSTALL_DIR/requirements.txt" -q
chmod +x "$INSTALL_DIR/scripts/start_council.sh" "$INSTALL_DIR/scripts/vps_setup.sh"
echo "==> Dependencies installed"

# --- Config (never overwrite existing secrets) ---
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
    cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
    echo "==> Created config.json from template"
else
    echo "==> config.json already exists (left unchanged)"
fi

# --- Environment file for secrets ---
ENV_FILE="$INSTALL_DIR/council.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$INSTALL_DIR/deploy/council.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "==> Created council.env — fill in your secrets before starting"
else
    echo "==> council.env already exists (left unchanged)"
fi

# --- Research directories ---
mkdir -p \
    "$INSTALL_DIR/research/transcripts" \
    "$INSTALL_DIR/research/reviews" \
    "$INSTALL_DIR/research/persona-patches" \
    "$INSTALL_DIR/research/memory"
touch "$INSTALL_DIR/research/memory/council_memory.md" 2>/dev/null || true
echo "==> Research directories ready"

# --- Hermes (optional) ---
if [[ "$INSTALL_HERMES" == true ]]; then
    if command -v hermes >/dev/null 2>&1; then
        echo "==> Hermes already installed"
    else
        echo "==> Installing Hermes Agent..."
        curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
    fi
    echo "==> Seeding Hermes profiles (requires hermes on PATH)..."
    if command -v hermes >/dev/null 2>&1; then
        cd "$INSTALL_DIR"
        python scripts/seed_hermes_profiles.py || echo "WARN: Hermes profile seeding failed — run manually after 'hermes setup'"
    else
        echo "WARN: hermes not on PATH after install. Log out/in or add to PATH, then run:"
        echo "      python scripts/seed_hermes_profiles.py"
    fi
fi

# --- systemd (optional) ---
if [[ "$INSTALL_SYSTEMD" == true ]]; then
    UNIT_PATH="/etc/systemd/system/council.service"
    echo "==> Installing systemd unit to $UNIT_PATH (requires sudo)..."
    sed \
        -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
        -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
        "$INSTALL_DIR/deploy/council.service.template" | sudo tee "$UNIT_PATH" >/dev/null
    sudo systemctl daemon-reload
    echo "==> systemd unit installed (not started — fill council.env first)"
    echo "    sudo systemctl enable --now council   # after secrets are set"
fi

# --- Preflight ---
echo ""
echo "==> Running preflight check..."
cd "$INSTALL_DIR"
python scripts/preflight_check.py || true

echo ""
echo "============================================"
echo " Setup complete. You still need to:"
echo "============================================"
echo " 1. Edit $ENV_FILE"
echo "      - LLM_API_KEY (Gemini)"
echo "      - DISCORD_BOT_TOKEN"
echo "      - DISCORD_READ_CHANNEL_ID"
echo "      - DISCORD_STATUS_CHANNEL_ID"
echo "      - DISCORD_ADMIN_USER_IDS (comma-separated)"
echo "      - WEBHOOK_CHAIR_CAT, WEBHOOK_BARNABY, WEBHOOK_CLEO, WEBHOOK_KIWI"
echo ""
echo " 2. Optionally edit $INSTALL_DIR/config.json for non-secret settings"
echo ""
echo " 3. Start manually:"
echo "      cd $INSTALL_DIR && set -a && source council.env && set +a"
echo "      $VENV_DIR/bin/python council_service.py"
echo ""
if [[ "$INSTALL_SYSTEMD" == true ]]; then
    echo "    Or via systemd (after council.env is filled):"
    echo "      sudo systemctl enable --now council"
fi
if [[ "$INSTALL_HERMES" != true ]]; then
    echo ""
    echo " Hermes not installed. To add later:"
    echo "      ./scripts/vps_setup.sh --install-hermes"
fi
echo "============================================"
