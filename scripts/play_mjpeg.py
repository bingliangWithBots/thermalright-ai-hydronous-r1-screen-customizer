#!/usr/bin/env python3
"""Low-CPU MJPEG file player for USBDisplay-compatible panels.

Pre-decode an MP4 into a concatenated MJPEG stream once, then loop those JPEG
frames with Python timing. This avoids a continuously running ffmpeg decode / FPS
filter / MJPEG encode pipeline during display playback.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("HYDRONOUS_ROOT", "/opt/thermalright-ai-hydronous-r1-screen"))
CTRL = Path(os.environ.get("HYDRONOUS_CONTROLLER", DEFAULT_ROOT / "scripts" / "hydronous_usbdisplay.py"))
MEDIA = Path(os.environ.get("HYDRONOUS_MJPEG", DEFAULT_ROOT / "media" / "startup.mjpg"))
FPS = float(os.environ.get("HYDRONOUS_FPS", "12"))
WIDTH = int(os.environ.get("HYDRONOUS_WIDTH", "960"))
HEIGHT = int(os.environ.get("HYDRONOUS_HEIGHT", "320"))

spec = importlib.util.spec_from_file_location("hydronous_usbdisplay", CTRL)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import controller: {CTRL}")
ctrl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctrl)


def load_mjpeg_frames(path: Path) -> list[bytes]:
    data = path.read_bytes()
    frames: list[bytes] = []
    pos = 0
    while True:
        start = data.find(b"\xff\xd8", pos)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start + 2)
        if end < 0:
            break
        frames.append(data[start:end + 2])
        pos = end + 2
    if not frames:
        raise SystemExit(f"no MJPEG frames found in {path}")
    return frames


def main() -> int:
    frames = load_mjpeg_frames(MEDIA)
    print(f"loaded {len(frames)} MJPEG frames from {MEDIA}; looping at {FPS} fps", file=sys.stderr)
    interval = 1.0 / max(FPS, 0.1)
    path = ctrl.find_device_path()
    with ctrl.UsbDisplay(path) as dev:
        dev.handshake()
        next_at = time.monotonic()
        idx = 0
        while True:
            now = time.monotonic()
            if now < next_at:
                time.sleep(next_at - now)
            dev.send_jpeg(frames[idx], WIDTH, HEIGHT)
            idx = (idx + 1) % len(frames)
            next_at += interval
            now = time.monotonic()
            if next_at < now - interval:
                next_at = now + interval
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
