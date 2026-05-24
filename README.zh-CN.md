# ThermalRight AI HydroNous R1 屏幕自定义工具

这是一个面向 ThermalRight AI HydroNous R1 风格 USB LCD 小屏，以及兼容 ChiZhu / GrandVision USBDisplay 协议面板的社区工具。

本项目包含 Linux `usbfs` 驱动、媒体播放脚本、低功耗预解码 MJPEG 播放模式，以及 systemd 开机自启模板。

## 功能

- 纯 Python + Linux `usbfs` 原始 USB bulk 通讯，不依赖 PyUSB/libusb。
- 自动查找兼容的 `87ad:70db` USBDisplay 设备。
- 按厂商协议发送 JPEG 帧。
- 支持 JPG/JPEG 静态图片和 MP4 视频。
- 支持 180° 旋转、contain/cover/stretch 缩放模式、可配置 FPS。
- 低功耗播放：先把 MP4 一次性预解码成 MJPEG，然后用 Python 循环发送 JPEG 帧，避免 ffmpeg 常驻。
- 提供 systemd 开机启动服务模板。

## 支持的设备类型

已知兼容设备特征：

```text
VID:PID   87ad:70db
Product   USBDISPLAY
Protocol  64 字节命令/帧头 + JPEG payload，经 USB bulk OUT 端点 0x01 发送
Typical logical resolution 960x320 landscape
```

类似硬件可能以不同品牌或水冷屏幕名称销售。可以先用下面命令确认：

```bash
lsusb
lsusb -d 87ad:70db -v
```

## 快速开始

安装 Python 依赖和 ffmpeg：

```bash
python3 -m pip install --user pillow
sudo apt install -y ffmpeg
```

探测设备：

```bash
sudo ./scripts/hydronous_usbdisplay.py probe
```

显示测试图：

```bash
sudo ./scripts/hydronous_usbdisplay.py test
```

直接播放 MP4：

```bash
sudo ./scripts/hydronous_usbdisplay.py --fit cover --fps 12 path/to/video.mp4
```

如果画面上下颠倒：

```bash
sudo ./scripts/hydronous_usbdisplay.py --flip --fit cover --fps 12 path/to/video.mp4
```

## 低功耗开机播放

直接播放 MP4 会让 ffmpeg 常驻运行。更省电的方式是先预解码：

```bash
mkdir -p media
./scripts/predecode_mjpeg.sh path/to/video.mp4 media/startup.mjpg 12
```

安装脚本和 systemd 服务：

```bash
sudo ./scripts/install_startup_service.sh
```

默认安装路径：

```text
/opt/thermalright-ai-hydronous-r1-screen
```

也可以自定义：

```bash
sudo INSTALL_ROOT=/opt/my-screen ./scripts/install_startup_service.sh
```

## 验证服务状态

```bash
systemctl is-enabled thermalright-ai-hydronous-r1-screen.service
systemctl is-active thermalright-ai-hydronous-r1-screen.service
systemctl --no-pager status thermalright-ai-hydronous-r1-screen.service --lines=60
journalctl -u thermalright-ai-hydronous-r1-screen.service -b --no-pager -n 80
```

低功耗模式下，应该没有 ffmpeg 常驻进程。正常播放进程应类似：

```text
python3 /opt/thermalright-ai-hydronous-r1-screen/scripts/play_mjpeg.py
```

## 非 root 访问的 udev 规则

原始 USB 访问通常需要 root。若希望 `plugdev` 组用户直接访问：

```bash
sudo tee /etc/udev/rules.d/99-hydronous-usbdisplay.rules <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="87ad", ATTR{idProduct}=="70db", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

如果权限没有立即变化，请拔插一次小屏。

## 常见问题

### 播放速度太快

只在 ffmpeg 里写 `fps=N` 不一定能控制真实播放速度，因为 `image2pipe` 会尽快输出帧。这个项目在 Python 发送循环里做了节流。如果仍然太快，可以降低 `--fps`。

### handshake 读取超时

某些面板在失败或过快的上一次 stream 后，会继续枚举在 USB 上，但不再回复 info-query 读取。控制器会在 handshake read timeout 时 fallback 到 `960x320`。如果后续发送帧也失败，请拔插 USB 小屏。

### systemd 服务 enable 了但开机没��动

不要同时写 `After=multi-user.target` 和 `WantedBy=multi-user.target`，这可能导致启动排序等待。本项目的 service 等待本地文件系统和 udev settle，不等待 multi-user.target 自身完成。

## English documentation

See [README.md](README.md).

## License

MIT
