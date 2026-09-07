#!/usr/bin/env bash
# Subscribe to the local UE MediaMTX HLS of the annotated YOLO stream.
set -euo pipefail
PATH_NAME="${MTX_LOCAL_PATH:-annotated}"
URL="${ANNOTATED_URL:-http://127.0.0.1:8888/${PATH_NAME}/index.m3u8}"
echo "watching $URL"
exec gst-launch-1.0 -e souphttpsrc location="$URL" ! hlsdemux ! decodebin ! fakesink sync=false
