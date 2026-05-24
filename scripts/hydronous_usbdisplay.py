#!/usr/bin/env python3
"""Tiny controller for ThermalRight AI HydroNous R1 / ChiZhu / GrandVision-style USB LCDs.

Known target device family:
  VID:PID 87ad:70db  ChiZhu Tech USBDISPLAY
  Bulk OUT endpoint 0x01, Bulk IN endpoint 0x81

Protocol: 64-byte handshake / frame header + JPEG payload over Linux usbfs.
No PyUSB/libusb dependency; uses only stdlib + Pillow for image generation.

Usage:
  sudo ./hydronous_usbdisplay.py probe
  sudo ./hydronous_usbdisplay.py test
  sudo ./hydronous_usbdisplay.py path/to/image.jpg
  sudo ./hydronous_usbdisplay.py --flip path/to/video.mp4
  sudo ./hydronous_usbdisplay.py clear

If you do not want sudo every time, install a udev rule printed by --udev-rule.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import fcntl
import os
import shutil
import struct
import subprocess
import sys
import textwrap
import time
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as e:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None

VID = 0x87AD
PID = 0x70DB
EP_OUT = 0x01
EP_IN = 0x81
IFACE = 0
HEADER_SIZE = 64
HANDSHAKE_READ_SIZE = 1024
BULK_TIMEOUT_MS = 5000
CHUNK_SIZE = 16 * 1024
IMAGE_EXTS = {".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4"}

# ioctl constants on Linux x86_64 from <linux/usbdevice_fs.h>.
USBDEVFS_BULK = 0xC0185502
USBDEVFS_CLAIMINTERFACE = 0x8004550F
USBDEVFS_RELEASEINTERFACE = 0x80045510

HANDSHAKE = bytearray(64)
HANDSHAKE[0:4] = b"\x12\x34\x56\x78"
HANDSHAKE[56] = 0x01  # device info query


def find_device_path(vid: int = VID, pid: int = PID) -> Path:
    root = Path("/sys/bus/usb/devices")
    candidates = []
    for dev in root.iterdir():
        try:
            v = int((dev / "idVendor").read_text().strip(), 16)
            p = int((dev / "idProduct").read_text().strip(), 16)
        except Exception:
            continue
        if v == vid and p == pid:
            bus = int((dev / "busnum").read_text().strip())
            num = int((dev / "devnum").read_text().strip())
            candidates.append(Path(f"/dev/bus/usb/{bus:03d}/{num:03d}"))
    if not candidates:
        raise SystemExit(f"USBDisplay {vid:04x}:{pid:04x} not found")
    return candidates[0]


class UsbDisplay:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None
        self.width = 320
        self.height = 960
        self.pm = None
        self.sub = None

    def open(self) -> None:
        try:
            self.fd = os.open(self.path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        except PermissionError:
            raise SystemExit(
                f"Permission denied opening {self.path}. Run with sudo, or install udev rule:\n"
                f"  sudo tee /etc/udev/rules.d/99-chizhu-usbdisplay.rules <<'EOF'\n"
                f"SUBSYSTEM==\"usb\", ATTR{{idVendor}}==\"87ad\", ATTR{{idProduct}}==\"70db\", MODE=\"0660\", GROUP=\"plugdev\"\n"
                f"EOF\n"
                f"  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
                f"Then unplug/replug the display."
            )
        fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", IFACE))

    def close(self) -> None:
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, USBDEVFS_RELEASEINTERFACE, struct.pack("I", IFACE))
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def bulk(self, ep: int, data: bytes | bytearray, timeout_ms: int = BULK_TIMEOUT_MS) -> int:
        if self.fd is None:
            raise RuntimeError("device not open")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        # Use a mutable ioctl argument. With immutable bytes, Python returns the
        # mutated argument bytes instead of the ioctl integer return value; this
        # broke bulk_read with TypeError when slicing by `n`.
        pkt = bytearray(struct.pack("IIIQ", ep, len(data), timeout_ms, ctypes.addressof(buf)))
        n = fcntl.ioctl(self.fd, USBDEVFS_BULK, pkt, True)
        return int(n)

    def bulk_read(self, ep: int, size: int, timeout_ms: int = BULK_TIMEOUT_MS) -> bytes:
        if self.fd is None:
            raise RuntimeError("device not open")
        buf = ctypes.create_string_buffer(size)
        pkt = bytearray(struct.pack("IIIQ", ep, size, timeout_ms, ctypes.addressof(buf)))
        n = fcntl.ioctl(self.fd, USBDEVFS_BULK, pkt, True)
        return bytes(buf.raw[: max(0, int(n))])

    def handshake(self) -> bytes:
        self.bulk(EP_OUT, HANDSHAKE, 1000)
        try:
            resp = self.bulk_read(EP_IN, HANDSHAKE_READ_SIZE, 1000)
        except TimeoutError:
            # Some ChiZhu panels stop answering the info-query endpoint after a
            # previous video stream until unplug/replug, but still accept JPEG
            # frame writes. Fall back to the common 960x320 resolution so
            # playback can recover without a physical USB reset.
            print("warning: handshake read timed out; assuming 960x320 and continuing", file=sys.stderr)
            self.pm = 17
            self.sub = 0
            self.width, self.height = 960, 320
            return b""
        if len(resp) < 57 or resp[24] == 0:
            raise RuntimeError(f"bad handshake: got {len(resp)} bytes; resp[24]={resp[24] if len(resp)>24 else None}")
        self.pm = resp[24]
        self.sub = resp[36] if len(resp) > 36 else 0
        w, h = pm_to_resolution(self.pm, self.sub)
        if w and h:
            self.width, self.height = w, h
        return resp

    def send_jpeg(self, jpeg: bytes, width: int, height: int) -> None:
        header = bytearray(HEADER_SIZE)
        header[0:4] = b"\x12\x34\x56\x78"
        put_le32(header, 4, 2)       # cmd=2 JPEG picture
        put_le32(header, 8, width)
        put_le32(header, 12, height)
        put_le32(header, 56, 2)      # mode
        put_le32(header, 60, len(jpeg))
        self.bulk(EP_OUT, header)
        for off in range(0, len(jpeg), CHUNK_SIZE):
            self.bulk(EP_OUT, jpeg[off: off + CHUNK_SIZE])
        if (HEADER_SIZE + len(jpeg)) % 512 == 0:
            self.bulk(EP_OUT, b"")


def put_le32(buf: bytearray, off: int, value: int) -> None:
    buf[off:off+4] = int(value).to_bytes(4, "little", signed=False)


def pm_to_resolution(pm: int, sub: int) -> tuple[int | None, int | None]:
    if pm == 32:
        return 320, 320
    if pm == 1 and sub == 48:
        return 1600, 720
    if pm == 1 and sub == 49:
        return 1920, 462
    table = {
        5: (320, 240), 7: (640, 480), 9: (854, 480), 10: (960, 540),
        11: (854, 480), 12: (800, 480), 13: (960, 320), 14: (640, 480),
        15: (640, 172), 16: (960, 540), 17: (960, 320), 50: (320, 240),
        63: (1600, 720), 64: (1600, 720), 65: (1920, 462), 66: (1920, 462),
        68: (1280, 480), 69: (1920, 440),
    }
    return table.get(pm, (None, None))


def jpeg_bytes(img: "Image.Image", quality: int = 90) -> bytes:
    import io
    bio = io.BytesIO()
    img.save(bio, format="JPEG", quality=quality, optimize=True)
    return bio.getvalue()


def make_test_image(width: int, height: int, message: str = "HydroNous R1 USBDisplay") -> "Image.Image":
    if Image is None:
        raise SystemExit("Pillow is required: python3 -m pip install --user pillow")
    img = Image.new("RGB", (width, height), (10, 12, 20))
    draw = ImageDraw.Draw(img)
    # Vertical color bands make orientation obvious on 320x960.
    bands = [
        (255, 80, 80), (255, 180, 60), (255, 240, 80),
        (80, 220, 120), (80, 180, 255), (160, 120, 255),
    ]
    band_h = max(1, height // len(bands))
    for i, color in enumerate(bands):
        y0 = i * band_h
        y1 = height if i == len(bands) - 1 else (i + 1) * band_h
        draw.rectangle([0, y0, width, y1], fill=color)
        draw.rectangle([12, y0 + 12, width - 12, y1 - 12], outline=(0, 0, 0), width=3)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([12, height//2 - 90, width - 12, height//2 + 110], radius=16, fill=(0, 0, 0, 190))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    font_big = load_font(28)
    font_small = load_font(18)
    lines = [message, f"{width}x{height}", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    y = height//2 - 58
    for line, font in [(lines[0], font_big), (lines[1], font_big), (lines[2], font_small)]:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2]-bbox[0])) // 2
        draw.text((x, y), line, fill=(255, 255, 255), font=font)
        y += (bbox[3]-bbox[1]) + 16
    return img


def load_font(size: int):
    if ImageFont is None:
        return None
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def apply_flip(img: "Image.Image", flip: bool) -> "Image.Image":
    """Optionally rotate content 180 degrees."""
    return img.rotate(180, expand=True) if flip else img


def fit_image(img: "Image.Image", width: int, height: int, fit: str = "contain") -> "Image.Image":
    """Fit an RGB image onto a black width×height canvas."""
    if Image is None:
        raise SystemExit("Pillow is required")
    img = img.convert("RGB")
    if fit == "stretch":
        return img.resize((width, height), Image.Resampling.LANCZOS)

    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", (width, height), (0, 0, 0))
    scale = max(width / src_w, height / src_h) if fit == "cover" else min(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if fit == "cover":
        left = max(0, (new_w - width) // 2)
        top = max(0, (new_h - height) // 2)
        return img.crop((left, top, left + width, top + height))
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    x = (width - new_w) // 2
    y = (height - new_h) // 2
    canvas.paste(img, (x, y))
    return canvas


def image_from_path(path: Path, width: int, height: int, flip: bool, fit: str) -> "Image.Image":
    if Image is None:
        raise SystemExit("Pillow is required: python3 -m pip install --user pillow")
    img = Image.open(path).convert("RGB")
    img = apply_flip(img, flip)
    return fit_image(img, width, height, fit)


def ffmpeg_filter(width: int, height: int, fps: float, flip: bool, fit: str) -> str:
    """Build an ffmpeg video filter for optional 180° flip + fit to target canvas."""
    parts: list[str] = []
    if flip:
        parts.append("hflip,vflip")
    if fit == "stretch":
        parts.append(f"scale={width}:{height}")
    elif fit == "cover":
        parts.append(
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    else:
        parts.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )
    parts.append(f"fps={fps}")
    return ",".join(parts)


def iter_mjpeg_frames(stream, *, max_buffer: int = 8 * 1024 * 1024):
    """Yield JPEG frames from an MJPEG byte stream."""
    buf = b""
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > max_buffer:
            # Keep enough tail to find a frame boundary; otherwise a broken
            # stream can grow memory without bound.
            buf = buf[-max_buffer:]
        while True:
            start = buf.find(b"\xff\xd8")
            if start < 0:
                buf = buf[-1:]
                break
            end = buf.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    buf = buf[start:]
                break
            frame = buf[start:end + 2]
            buf = buf[end + 2:]
            yield frame


def stream_video(dev: UsbDisplay, path: Path, width: int, height: int, args) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit(
            "Video support needs ffmpeg. Install once with:\n"
            "  sudo apt update && sudo apt install -y ffmpeg\n"
            "Images/test/clear work without ffmpeg."
        )
    if not path.exists():
        raise SystemExit(f"video not found: {path}")
    vf = ffmpeg_filter(width, height, args.fps, args.flip, args.fit)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if args.loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(path), "-an", "-vf", vf, "-q:v", str(args.quality), "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    print("ffmpeg:", " ".join(cmd), file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    start = time.monotonic()
    frames = 0
    interval = 1.0 / max(args.fps, 0.1)
    next_frame_at = start
    try:
        for frame in iter_mjpeg_frames(proc.stdout):
            now = time.monotonic()
            if now < next_frame_at:
                time.sleep(next_frame_at - now)
            dev.send_jpeg(frame, width, height)
            frames += 1
            next_frame_at += interval
            # If USB writes fall behind badly, resync instead of accumulating lag.
            now = time.monotonic()
            if next_frame_at < now - interval:
                next_frame_at = now + interval
            if args.duration is not None and args.duration > 0 and now - start >= args.duration:
                break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        print(f"sent {frames} video frame(s)")



def resolve_size(args, dev: UsbDisplay | None = None) -> tuple[int, int]:
    if args.width and args.height:
        return args.width, args.height
    if dev and dev.width and dev.height:
        w, h = dev.width, dev.height
        # Device PM=17 reports 960x320. Keep that landscape orientation by
        # default; use --portrait to intentionally map it to 320x960.
        if args.portrait and w == 960 and h == 320:
            return 320, 960
        return w, h
    return args.width or 960, args.height or 320



def resolve_target(target: str) -> tuple[str, Path | None]:
    """Resolve target into command and optional media path.

    Reserved words are `probe`, `test`, and `clear`. Everything else is treated
    as a media path and classified by file suffix.
    """
    if target in {"probe", "test", "clear"}:
        return target, None
    path = Path(target).expanduser()
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return "image", path
    if suffix in VIDEO_EXTS:
        return "video", path
    raise SystemExit(
        f"Cannot infer media type from suffix {suffix!r}: {target}\n"
        f"Supported image suffixes: {', '.join(sorted(IMAGE_EXTS))}\n"
        f"Supported video suffixes: {', '.join(sorted(VIDEO_EXTS))}"
    )

def print_udev_rule() -> None:
    print('SUBSYSTEM=="usb", ATTR{idVendor}=="87ad", ATTR{idProduct}=="70db", MODE="0660", GROUP="plugdev"')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", type=Path, default=None, help="/dev/bus/usb/BBB/DDD; auto-detect by default")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--quality", type=int, default=90)
    parser.add_argument("--flip", "--rotate-180", action="store_true", help="Rotate content 180 degrees")
    parser.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain", help="How to fit image/video into the LCD canvas")
    parser.add_argument("--fps", type=float, default=15.0, help="Frame rate for static resend/video decode")
    parser.add_argument("--duration", type=float, default=None, help="Seconds to keep sending; default is 0 = forever. Use a positive value to stop.")
    parser.add_argument("--no-loop", dest="loop", action="store_false", default=True, help="Do not loop video; stop at EOF")
    parser.add_argument("--portrait", action=argparse.BooleanOptionalAction, default=False, help="Map 960x320 probe to 320x960 portrait")
    parser.add_argument("--udev-rule", action="store_true", help="Print udev rule and exit")
    parser.add_argument("target", nargs="?", default="test", help="probe/test/clear or a media file path. Image/video is inferred from suffix.")
    parser.add_argument("extra", nargs="*", help="Optional test message words")
    args = parser.parse_args(argv)

    if args.udev_rule:
        print_udev_rule()
        return 0

    path = args.device or find_device_path()
    cmd, media_path = resolve_target(args.target)
    with UsbDisplay(path) as dev:
        resp = dev.handshake()
        probed = pm_to_resolution(dev.pm or 0, dev.sub or 0)
        print(f"device={path} pm={dev.pm} sub={dev.sub} probed={probed[0]}x{probed[1]}")
        if cmd == "probe":
            print("resp[0:64]=" + resp[:64].hex(" "))
            return 0
        width, height = resolve_size(args, dev)
        if cmd == "video":
            stream_video(dev, media_path, width, height, args)
            return 0
        if cmd == "test":
            message = " ".join(args.extra).strip() or "HydroNous R1 USBDisplay"
            img = make_test_image(width, height, message)
            img = apply_flip(img, args.flip)
            img = fit_image(img, width, height, args.fit)
        elif cmd == "clear":
            if Image is None:
                raise SystemExit("Pillow is required")
            img = Image.new("RGB", (width, height), (0, 0, 0))
        elif cmd == "image":
            img = image_from_path(media_path, width, height, args.flip, args.fit)
        else:
            raise SystemExit(f"unknown command {cmd}")
        payload = jpeg_bytes(img, args.quality)
        interval = 1.0 / max(args.fps, 0.1)
        duration = 0.0 if args.duration is None else args.duration
        print(f"sending jpeg {width}x{height}, {len(payload)} bytes, duration={duration}s fps={args.fps} flip={args.flip} fit={args.fit}")
        start = time.monotonic()
        frames = 0
        while True:
            dev.send_jpeg(payload, width, height)
            frames += 1
            if duration > 0 and time.monotonic() - start >= duration:
                break
            time.sleep(interval)
        print(f"sent {frames} frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
