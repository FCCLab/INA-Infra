#!/usr/bin/env bash
# Download Big Buck Bunny (~10 min, Creative Commons) into data/source.mp4.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT="${1:-$ROOT/data/source.mp4}"
# Blender Foundation 1080p MP4 mirror (stable public URL).
URL="${HDSTREAM_VIDEO_URL:-https://archive.org/download/BigBuckBunny_124/Content/big_buck_bunny_720p_surround.mp4}"

mkdir -p "$(dirname "$OUT")"
if [[ -s "$OUT" ]]; then
  echo "already present: $OUT ($(wc -c <"$OUT") bytes)"
  exit 0
fi

TMP="${OUT}.partial"
echo "Downloading $URL -> $OUT"
curl -fL --retry 5 --retry-delay 2 -o "$TMP" "$URL"
mv -f "$TMP" "$OUT"
echo "done: $OUT ($(wc -c <"$OUT") bytes)"
