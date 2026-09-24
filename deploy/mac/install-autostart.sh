#!/usr/bin/env bash
# Run once:  bash deploy/mac/install-autostart.sh
# Starts the bot now and at every login, restarts it if it ever stops,
# and keeps the Mac awake (while plugged in) as long as the bot runs.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/com.tradingbot.plist"

"$PY" -m pip install -q --user -r "$DIR/requirements.txt"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.tradingbot</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-s</string>
    <string>$PY</string><string>-u</string><string>-m</string><string>trading_bot.bot</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$DIR/bot.log</string>
  <key>StandardErrorPath</key><string>$DIR/bot.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Done! The bot is running and will restart on its own, including after you log in again."
echo "See what it's doing:  tail -f \"$DIR/bot.log\""
echo "Stop it for good:     launchctl unload \"$PLIST\" && rm \"$PLIST\""
