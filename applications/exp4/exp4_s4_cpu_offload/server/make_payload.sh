#!/usr/bin/env bash
# Build a 1 MB SFTP download object (optional; the server queue generates these).
set -euo pipefail
OUT="${1:-/home/ina/download/exp4-1mb.bin}"
mkdir -p "$(dirname "$OUT")"
dd if=/dev/urandom of="$OUT" bs=1M count=1 status=none
chmod 644 "$OUT"
echo "wrote $OUT ($(wc -c < "$OUT") bytes)"
