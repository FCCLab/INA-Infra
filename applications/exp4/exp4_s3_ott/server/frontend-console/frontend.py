#!/usr/bin/env python3
"""Exp4 frontend console: static UI + reverse-proxy to the local backend."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles


def _find_static_dir() -> Path:
    env_dir = os.environ.get("DASHBOARD_STATIC")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)
    here = Path(__file__).resolve().parent
    for cand in (here / "static", Path("/app/frontend-console/static"), Path("/app/static")):
        if cand.is_dir():
            return cand
    return here / "static"


STATIC_DIR = _find_static_dir()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8080").rstrip("/")
APP_TITLE = os.environ.get("CONSOLE_TITLE", "Exp4 console")

app = FastAPI(title=APP_TITLE, docs_url=None)


def _forward(method: str, path: str, data: bytes | None) -> Response:
    url = f"{BACKEND_URL}/api/{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    timeout = 180 if path in ("download", "run") else 60
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "application/json")
            return Response(content=raw, status_code=resp.status, media_type=ctype)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return Response(content=raw, status_code=exc.code, media_type="application/json")
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"backend unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")


@app.get("/download")
def proxy_download() -> Response:
    """Stream backend GET /download (S4 encrypt queue) without buffering the zip."""
    url = f"{BACKEND_URL}/download"
    req = urllib.request.Request(url, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        ctype = exc.headers.get("Content-Type", "application/json") if exc.headers else "application/json"
        return Response(content=raw, status_code=exc.code, media_type=ctype)
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"backend unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")

    def _gen():
        try:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()

    headers = {}
    for key in ("X-Proc-Ms", "X-File-Id", "X-Plain-Bytes", "Content-Length", "Content-Disposition"):
        val = resp.headers.get(key)
        if val:
            headers[key] = val
    return StreamingResponse(_gen(), media_type="application/zip", headers=headers)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "backend": BACKEND_URL, "title": APP_TITLE}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(path: str, request: Request) -> Response:
    data = None
    if request.method not in ("GET", "HEAD"):
        data = await request.body()
    return _forward(request.method, path, data)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("FRONTEND_PORT", "80")))
