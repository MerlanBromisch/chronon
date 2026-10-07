#!/usr/bin/env bash
# Build the app with PyInstaller into dist/ (run fetch-ffmpeg.sh first).
set -euo pipefail
cd "$(dirname "$0")/.."
sep=":"; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) sep=";" ;; esac
args=()
for f in packaging/bin/*; do args+=(--add-binary "$f${sep}bin"); done
uv run --extra gui --group build pyinstaller --noconfirm --clean --windowed \
  --name Chronon --distpath dist --workpath build \
  --exclude-module tkinter "${args[@]}" packaging/launch.py
