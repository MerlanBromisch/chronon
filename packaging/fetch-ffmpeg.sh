#!/usr/bin/env bash
# Download static ffmpeg + ffprobe for this platform into packaging/bin.
# Windows/Linux: BtbN LGPL builds. macOS arm64: martin-riedl.de (GPL build; no LGPL
# download exists for macOS — open question in docs/app.md).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf bin tmp && mkdir -p bin tmp
case "$(uname -s)" in
  Darwin)
    for tool in ffmpeg ffprobe; do
      curl -fsSL -o "tmp/$tool.zip" "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$tool.zip"
      unzip -q -o "tmp/$tool.zip" -d bin
    done
    chmod +x bin/ffmpeg bin/ffprobe ;;
  Linux)
    curl -fsSL -o tmp/f.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-lgpl.tar.xz
    tar -xJf tmp/f.tar.xz -C tmp
    cp tmp/ffmpeg-*/bin/ffmpeg tmp/ffmpeg-*/bin/ffprobe bin/ ;;
  MINGW*|MSYS*|CYGWIN*)
    curl -fsSL -o tmp/f.zip https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip
    unzip -q tmp/f.zip -d tmp
    cp tmp/ffmpeg-*/bin/ffmpeg.exe tmp/ffmpeg-*/bin/ffprobe.exe bin/ ;;
esac
rm -rf tmp
ls -l bin
