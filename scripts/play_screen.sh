#!/usr/bin/env bash
set -euo pipefail
pkill -f "hydronous_usbdisplay.py.*\.mp4" 2>/dev/null || true
pkill -f "play_mjpeg.py" 2>/dev/null || true
exec python3 /opt/thermalright-ai-hydronous-r1-screen/scripts/play_mjpeg.py
