#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd "$(dirname "$0")/.." && pwd -P)
SERVICE_NAME=caregiver-handoff.service
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [--check|--install|--help]\n' "$0" >&2
    exit 2
fi

case "${1:---install}" in
    --help)
        printf 'Usage: %s [--check|--install|--help]\n' "$0"
        printf '  --check    Inspect Pi prerequisites without making changes.\n'
        printf '  --install  Install Python dependencies and an auto-start service.\n'
        exit 0
        ;;
    --check|--install)
        MODE=${1:---install}
        ;;
    *)
        printf 'Usage: %s [--check|--install|--help]\n' "$0" >&2
        exit 2
        ;;
esac

if [ "$(uname -s)" != Linux ]; then
    printf 'Run this script on the Raspberry Pi, not on this computer.\n' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    printf 'Python 3.10 or newer is required on the Pi.\n' >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    printf 'A systemd-based Linux installation is required.\n' >&2
    exit 1
fi

printf 'App directory: %s\n' "$APP_DIR"
python3 --version
printf 'Architecture: %s\n' "$(uname -m)"
if command -v nginx >/dev/null 2>&1; then
    if systemctl is-active --quiet nginx; then
        printf 'nginx: active\n'
    else
        printf 'nginx: installed, not active\n'
    fi
else
    printf 'nginx: not installed\n'
fi

if [ "$MODE" = --check ]; then
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        printf '%s: active\n' "$SERVICE_NAME"
    else
        printf '%s: not active\n' "$SERVICE_NAME"
    fi
    if command -v curl >/dev/null 2>&1; then
        if [ "$(curl -fsS --max-time 3 http://127.0.0.1:3000/health 2>/dev/null)" = '{"status":"ok"}' ]; then
            printf 'App on port 3000: healthy\n'
        else
            printf 'App on port 3000: not confirmed\n'
        fi
        if [ "$(curl -fsS --max-time 3 http://127.0.0.1/health 2>/dev/null)" = '{"status":"ok"}' ]; then
            printf 'nginx proxy on port 80: healthy\n'
        else
            printf 'nginx proxy on port 80: not confirmed\n'
        fi
    fi
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    printf 'Run as the normal Pi user, not as root.\n' >&2
    exit 1
fi
case "$APP_DIR" in
    *[!A-Za-z0-9_./-]*)
        printf 'Move the project to a path without spaces or special characters before installing the service.\n' >&2
        exit 1
        ;;
esac
if ! command -v sudo >/dev/null 2>&1; then
    printf 'sudo is required to install the systemd service.\n' >&2
    exit 1
fi
if { [ -e "$APP_DIR/.venv" ] || [ -L "$APP_DIR/.venv" ]; } &&
    [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    printf 'Existing .venv is unusable on this Pi. Create a fresh Pi virtual environment before continuing.\n' >&2
    exit 1
fi
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    if ! python3 -m venv "$APP_DIR/.venv"; then
        printf 'Could not create .venv. Install python3-venv on the Pi and retry.\n' >&2
        exit 1
    fi
fi
if ! "$APP_DIR/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
    printf 'pip is missing from .venv. Install python3-venv on the Pi and retry.\n' >&2
    exit 1
fi
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

umask 077
mkdir -p "$APP_DIR/data"
chmod 700 "$APP_DIR/data"
SESSION_FILE="$APP_DIR/data/session.env"
if [ -L "$SESSION_FILE" ]; then
    printf 'Refusing to use a session secret file that is a symlink.\n' >&2
    exit 1
fi
if [ ! -e "$SESSION_FILE" ]; then
    "$APP_DIR/.venv/bin/python" - "$SESSION_FILE" <<'PY'
import secrets
import sys
from pathlib import Path

with Path(sys.argv[1]).open("x", encoding="ascii") as session:
    session.write("HANDOFF_SESSION_SECRET=" + secrets.token_urlsafe(48) + "\n")
PY
fi
chmod 600 "$SESSION_FILE"

UNIT_FILE=$(mktemp)
trap 'rm -f "$UNIT_FILE"' EXIT HUP INT TERM
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Offline Caregiver Handoff Board
After=network.target

[Service]
Type=simple
User=$(id -un)
Group=$(id -gn)
WorkingDirectory=$APP_DIR
Environment=HOST=127.0.0.1
Environment=PORT=3000
EnvironmentFile=$SESSION_FILE
ExecStart=$APP_DIR/start.sh
Restart=on-failure
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

EXISTING_SERVICE=no
if [ -e "$SERVICE_FILE" ] || [ -L "$SERVICE_FILE" ]; then
    if ! cmp -s "$UNIT_FILE" "$SERVICE_FILE"; then
        printf 'Existing %s differs. Review it manually; this script will not overwrite it.\n' "$SERVICE_FILE" >&2
        exit 1
    fi
    EXISTING_SERVICE=yes
else
    sudo install -m 644 "$UNIT_FILE" "$SERVICE_FILE"
fi
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
if [ "$EXISTING_SERVICE" = yes ]; then
    sudo systemctl restart "$SERVICE_NAME"
fi

if command -v curl >/dev/null 2>&1; then
    READY=no
    for attempt in 1 2 3 4 5; do
        if [ "$(curl -fsS --max-time 3 http://127.0.0.1:3000/health 2>/dev/null)" = '{"status":"ok"}' ]; then
            READY=yes
            break
        fi
        sleep 1
    done
    if [ "$READY" != yes ]; then
        printf 'App did not answer on port 3000. Check: journalctl -u %s -n 50\n' "$SERVICE_NAME" >&2
        exit 1
    fi
    printf 'App health check: OK on 127.0.0.1:3000\n'
    if systemctl is-active --quiet nginx &&
        [ "$(curl -fsS --max-time 3 http://127.0.0.1/health 2>/dev/null)" = '{"status":"ok"}' ]; then
        printf 'nginx proxy on port 80: OK\n'
    else
        printf 'nginx proxy on port 80: not confirmed; existing nginx configuration was not changed.\n'
    fi
else
    printf 'Install curl or check http://127.0.0.1:3000/health manually.\n'
fi
