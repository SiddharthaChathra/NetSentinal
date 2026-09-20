#!/usr/bin/env bash
# Keep the NetSentinel agent running on Linux (systemd user service).
#
# The agent only reports while its process is alive, so closing the terminal
# or rebooting stops it — and the device then shows as offline even though the
# machine is fine. This installs a user service that starts the agent at login
# and restarts it if it dies.
#
# Run from the repo root:
#     bash agent/install_autostart.sh
#
# Remove it again with:
#     systemctl --user disable --now netsentinel-agent
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT="$REPO_ROOT/agent/agent.py"
SERVICE_NAME="netsentinel-agent"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

[ -f "$AGENT" ] || { echo "Could not find $AGENT. Run this from the NetSentinel repo." >&2; exit 1; }

if [ ! -f "$REPO_ROOT/.env" ]; then
  echo "Warning: no .env in $REPO_ROOT — the agent needs API_BASE_URL and AGENT_TOKEN." >&2
  echo "Get them from the app's 'Add a device' panel." >&2
fi

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

command -v systemctl >/dev/null 2>&1 || {
  echo "systemd not available. Run the agent in the background instead:" >&2
  echo "  nohup \"$PYTHON\" \"$AGENT\" --start >/tmp/netsentinel-agent.log 2>&1 &" >&2
  exit 1
}

echo "Repo:   $REPO_ROOT"
echo "Python: $PYTHON"

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$SERVICE_NAME.service" <<UNIT
[Unit]
Description=NetSentinel agent (reports this machine's network telemetry)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
ExecStart=$PYTHON $AGENT --start
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"

# Without this the service stops when the user logs out.
loginctl enable-linger "$USER" >/dev/null 2>&1 || \
  echo "Note: could not enable linger; the agent will stop when you log out."

echo
echo "Done. '$SERVICE_NAME' is enabled and running."
echo "The device should show ONLINE in NetSentinel within a minute."
echo
echo "  Check it:  systemctl --user status $SERVICE_NAME"
echo "  Logs:      journalctl --user -u $SERVICE_NAME -f"
echo "  Remove it: systemctl --user disable --now $SERVICE_NAME"
