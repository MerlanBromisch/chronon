#!/usr/bin/env bash
# Build the app with PyInstaller into dist/ (run fetch-ffmpeg.sh first).
set -euo pipefail
cd "$(dirname "$0")/.."
sep=":"; icon=packaging/icon/Chronon.ico
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) sep=";" ;;
  Darwin) icon=packaging/icon/Chronon.icns ;;
esac
args=()
for f in packaging/bin/*; do args+=(--add-binary "$f${sep}bin"); done
if [ -f "$icon" ]; then args+=(--icon "$icon"); fi
args+=(--add-data "src/chronon/gui/icon.png${sep}chronon/gui")  # the window icon
uv run --extra gui --group build pyinstaller --noconfirm --clean --windowed \
  --name Chronon --distpath dist --workpath build \
  --osx-bundle-identifier io.github.merlanbromisch.chronon \
  --exclude-module tkinter "${args[@]}" packaging/launch.py

if [ "$(uname -s)" = Darwin ]; then
  # the version Finder and "About" show (PyInstaller writes 0.0.0), then sign again (ad hoc):
  # changing Info.plist breaks the signature it made
  version=$(uv run python -c "import chronon; print(chronon.__version__)")
  plist=dist/Chronon.app/Contents/Info.plist
  plutil -replace CFBundleShortVersionString -string "$(echo "$version" | sed -E 's/^([0-9.]+).*/\1/')" "$plist"
  plutil -replace CFBundleVersion -string "$version" "$plist"
  plutil -replace NSHumanReadableCopyright -string "MIT License" "$plist"
  codesign --force --deep --sign - dist/Chronon.app
fi
