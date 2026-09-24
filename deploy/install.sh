#!/usr/bin/env bash
# One-command setup on an always-on Linux server (Ubuntu/Debian).
# Installs the bot as a system service that starts on boot and restarts within
# 5 seconds if it ever crashes. Run from the project folder:  bash deploy/install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
SERVICE=/etc/systemd/system/trading-bot.service

echo "==> Installing Python"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv >/dev/null

echo "==> Setting up the bot in $DIR"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
  echo "==> Paste your Alpaca PAPER keys (from app.alpaca.markets -> API Keys)"
  read -rp "API Key ID: " KEY
  read -rsp "Secret Key: " SECRET; echo
  printf 'ALPACA_API_KEY_ID=%s\nALPACA_API_SECRET_KEY=%s\nALPACA_BASE_URL=https://paper-api.alpaca.markets/v2\n' \
    "$KEY" "$SECRET" > "$DIR/.env"
  chmod 600 "$DIR/.env"
fi

echo "==> Checking the keys work"
(cd "$DIR" && "$DIR/.venv/bin/python" -m trading_bot.bot --dry-run --once)

echo "==> Installing the always-on service"
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=AI paper trading bot
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -u -m trading_bot.bot
Restart=always
RestartSec=5
StandardOutput=append:$DIR/bot.log
StandardError=append:$DIR/bot.log

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot

echo
echo "Done! The bot is running and will keep running, even after crashes or reboots."
echo "  Watch it live:   tail -f $DIR/bot.log"
echo "  Status:          sudo systemctl status trading-bot"
echo "  Stop / start:    sudo systemctl stop trading-bot  /  sudo systemctl start trading-bot"
echo "  Update:          git pull && sudo systemctl restart trading-bot"
