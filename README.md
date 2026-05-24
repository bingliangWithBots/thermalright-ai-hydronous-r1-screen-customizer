# ThermalRight AI HydroNous R1 Screen Customizer

[中文文档](README.zh-CN.md)

Community tooling for customizing the USB LCD screen used by ThermalRight AI HydroNous R1-style coolers and compatible ChiZhu / GrandVision USBDisplay panels.

The project provides a Linux `usbfs` driver, media playback helpers, a low-power predecoded-MJPEG playback mode, and a systemd startup service template.

## Preview

![Screen preview](media/preview.gif)

## Features

- Raw USB bulk driver with no PyUSB/libusb dependency.
- Auto-detects compatible `87ad:70db` USBDisplay devices.
- Sends JPEG frames using the vendor frame protocol.
- Supports JPG/JPEG still images and MP4 video playback.
- Optional 180° rotation, contain/cover/stretch fitting, and configurable FPS.
- Low-power playback mode: predecode MP4 to MJPEG once, then loop JPEG frames without a continuous ffmpeg process.
- systemd service template for boot-time playback.

## Supported device family

Known compatible device shape:

```text
VID:PID   87ad:70db
Product   USBDISPLAY
Protocol  64-byte command/header + JPEG payload over USB bulk OUT endpoint 0x01
Typical logical resolution 960x320 landscape
```

Compatible hardware may be sold under different vendor or cooler names. Confirm with:

```bash
lsusb
lsusb -d 87ad:70db -v
```

## Agent guidance

If an automation agent is installing this project for a user, assume a Linux host with `sudo`, `systemd`, and the USB LCD already attached internally. Do not suggest physical USB reconnection for this display; if USB permissions or streaming get stale, restart the service or reboot the host.

Use this non-interactive install path:

```bash
sudo apt update
sudo apt install -y git python3 python3-pip ffmpeg
python3 -m pip install --user pillow
git clone https://github.com/bingliangWithBots/thermalright-ai-hydronous-r1-screen-customizer.git
cd thermalright-ai-hydronous-r1-screen-customizer
sudo ./scripts/hydronous_usbdisplay.py probe
sudo ./scripts/hydronous_usbdisplay.py test
```

For boot-time low-power playback, first place or generate `media/startup.mjpg`, then install the service:

```bash
mkdir -p media
./scripts/predecode_mjpeg.sh path/to/video.mp4 media/startup.mjpg 12
sudo ./scripts/install_startup_service.sh
systemctl --no-pager status thermalright-ai-hydronous-r1-screen.service --lines=30
```

## Quick start

Install Python dependencies and ffmpeg:

```bash
python3 -m pip install --user pillow
sudo apt install -y ffmpeg
```

Probe the device:

```bash
sudo ./scripts/hydronous_usbdisplay.py probe
```

Show a test card:

```bash
sudo ./scripts/hydronous_usbdisplay.py test
```

Play an MP4 directly:

```bash
sudo ./scripts/hydronous_usbdisplay.py --fit cover --fps 12 path/to/video.mp4
```

If the image is upside down:

```bash
sudo ./scripts/hydronous_usbdisplay.py --flip --fit cover --fps 12 path/to/video.mp4
```

## Low-power startup playback

Direct MP4 playback runs ffmpeg continuously. For lower CPU use, predecode once:

```bash
mkdir -p media
./scripts/predecode_mjpeg.sh path/to/video.mp4 media/startup.mjpg 12
```

Install the scripts and systemd service:

```bash
sudo ./scripts/install_startup_service.sh
```

The default install path is:

```text
/opt/thermalright-ai-hydronous-r1-screen
```

Override it with:

```bash
sudo INSTALL_ROOT=/opt/my-screen ./scripts/install_startup_service.sh
```

## Service verification

```bash
systemctl is-enabled thermalright-ai-hydronous-r1-screen.service
systemctl is-active thermalright-ai-hydronous-r1-screen.service
systemctl --no-pager status thermalright-ai-hydronous-r1-screen.service --lines=60
journalctl -u thermalright-ai-hydronous-r1-screen.service -b --no-pager -n 80
```

In low-power mode, there should be no continuous ffmpeg process. The expected playback process is:

```text
python3 /opt/thermalright-ai-hydronous-r1-screen/scripts/play_mjpeg.py
```

## Udev rule for non-root access

Raw USB access usually requires root. To allow a normal user in `plugdev` to access the display:

```bash
sudo tee /etc/udev/rules.d/99-hydronous-usbdisplay.rules <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="87ad", ATTR{idProduct}=="70db", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## Troubleshooting

### Playback is too fast

Do not rely only on `ffmpeg -vf fps=N` when ffmpeg writes to `image2pipe`; it can emit frames as fast as the USB writer accepts them. This driver throttles in Python. Lower `--fps` if the panel still feels fast.

### Handshake read timeout

Some panels stop answering the info-query read after a failed or overly fast stream, while still accepting frame writes. The controller catches handshake read timeout and falls back to `960x320`. If frame writes still fail, restart the playback service or reboot the host.

### Startup service enabled but not starting

Do not order the service with both `After=multi-user.target` and `WantedBy=multi-user.target`; that can leave it waiting at boot. The included service waits for local files and udev settle instead.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
