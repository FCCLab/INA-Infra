#!/usr/bin/env bash
# Build the 5 MB SFTP download object used by Exp4 slice 1.
set -euo pipefail
OUT="${1:-/home/ina/download/exp4-5mb.bin}"
mkdir -p "$(dirname "$OUT")"
dd if=/dev/urandom of="$OUT" bs=1M count=5 status=none
chmod 644 "$OUT"
echo "wrote $OUT ($(wc -c < "$OUT") bytes)"
