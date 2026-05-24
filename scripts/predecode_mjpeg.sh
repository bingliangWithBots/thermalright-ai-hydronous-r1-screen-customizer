#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 1 ]; then
  echo "usage: $0 input.mp4 [output.mjpg] [fps]" >&2
  exit 2
fi
input="$1"
output="${2:-startup.mjpg}"
fps="${3:-12}"
ffmpeg -y -hide_banner -loglevel error   -i "$input"   -an -vf "fps=${fps}"   -q:v 90 -f image2pipe -vcodec mjpeg   "$output"
echo "wrote $output"
