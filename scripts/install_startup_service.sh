#!/usr/bin/env bash
set -euo pipefail
SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/thermalright-ai-hydronous-r1-screen}"
SERVICE_NAME="thermalright-ai-hydronous-r1-screen.service"

install -d -m 0755 "$INSTALL_ROOT/scripts" "$INSTALL_ROOT/media"
install -m 0755 "$SRC_ROOT/scripts/hydronous_usbdisplay.py" "$INSTALL_ROOT/scripts/hydronous_usbdisplay.py"
install -m 0755 "$SRC_ROOT/scripts/play_mjpeg.py" "$INSTALL_ROOT/scripts/play_mjpeg.py"
install -m 0755 "$SRC_ROOT/scripts/play_screen.sh" "$INSTALL_ROOT/scripts/play_screen.sh"
install -m 0755 "$SRC_ROOT/scripts/predecode_mjpeg.sh" "$INSTALL_ROOT/scripts/predecode_mjpeg.sh"
install -m 0644 "$SRC_ROOT/scripts/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"

if [ -f "$SRC_ROOT/media/startup.mjpg" ]; then
  install -m 0644 "$SRC_ROOT/media/startup.mjpg" "$INSTALL_ROOT/media/startup.mjpg"
fi

systemctl cancel "$SERVICE_NAME" 2>/dev/null || true
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl --no-pager status "$SERVICE_NAME" --lines=30
