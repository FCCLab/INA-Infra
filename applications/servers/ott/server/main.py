"""Main server launcher for OTT Video Streaming & Orchestration."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

import uvicorn
from prometheus_client import start_http_server

from common import metrics
from server.api import app as fastapi_app
import server.api as api_module
from server.ott import OttEngine

logging.basicConfig(
    level=logging.INFO,
    format='{"ts": %(created)f, "level": "%(levelname)s", "module": "%(name)s", "msg": "%(message)s"}',
)
logger = logging.getLogger("ott.main")


def main():
    http_port = int(os.environ.get("HTTP_PORT", "8080"))
    metrics_port = int(os.environ.get("METRICS_PORT", "9103"))
    mtx_rtsp = os.environ.get("MTX_RTSP_URL", "rtsp://127.0.0.1:8555")

    logger.info(f"Starting OTT Video Streaming Server (HTTP :{http_port}, Metrics :{metrics_port})")

    # 1. Start Prometheus Exporter
    try:
        start_http_server(metrics_port)
        logger.info(f"Prometheus metrics listening on :{metrics_port}/metrics")
    except Exception as e:
        logger.warning(f"Could not start Prometheus HTTP server on :{metrics_port}: {e}")

    # 2. Start Multi-Channel Video Engine
    engine = OttEngine(mtx_rtsp_base=mtx_rtsp)
    api_module.OTT_ENGINE = engine
    engine.start()

    # 4. Start FastAPI server
    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=http_port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
