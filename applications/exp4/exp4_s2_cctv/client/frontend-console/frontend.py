#!/usr/bin/env python3
"""CCTV UE frontend: console UI + proxy to local backend."""
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
    for cand in [Path("/app/ue/static"), Path("/app/static"), Path(__file__).parent / "static"]:
        if cand.is_dir():
            return cand
    return Path("/app/ue/static")


STATIC_DIR = _find_static_dir()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8090").rstrip("/")
MTX_HLS_URL = os.environ.get("MTX_HLS_URL", "http://127.0.0.1:8888").rstrip("/")
MTX_WHEP_URL = os.environ.get("MTX_WHEP_URL", "http://127.0.0.1:8889").rstrip("/")
UE_NAME = os.environ.get("UE_NAME", "cctv-ue")
CONSOLE_IP = os.environ.get("CONSOLE_IP", "")
CONSOLE_MAC = os.environ.get("CONSOLE_MAC", "")

app = FastAPI(title=f"{UE_NAME} console", docs_url=None)


def _forward(method: str, path: str, data: bytes | None) -> Response:
    url = f"{BACKEND_URL}/api/{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "application/json")
            return Response(content=raw, status_code=resp.status, media_type=ctype)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return Response(content=raw, status_code=exc.code, media_type="application/json")
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"backend unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "ue": UE_NAME, "console_ip": CONSOLE_IP, "console_mac": CONSOLE_MAC}


def _stream_backend(path: str) -> Response:
    url = f"{BACKEND_URL}/api/{path}"
    req = urllib.request.Request(url)
    try:
        resp = urllib.request.urlopen(req, timeout=None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return Response(content=raw, status_code=exc.code, media_type="application/json")
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"backend unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")

    def gen():
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                resp.close()
            except Exception:
                pass

    ctype = resp.headers.get("Content-Type", "application/octet-stream")
    return StreamingResponse(
        gen(),
        media_type=ctype,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _proxy_mtx_hls(path: str, request: Request) -> Response:
    qs = str(request.query_params)
    url = f"{MTX_HLS_URL}/{path}"
    if qs:
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, method="GET")
    range_hdr = request.headers.get("range")
    if range_hdr and request.method != "HEAD":
        req.add_header("Range", range_hdr)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            if path.endswith(".m3u8") and body:
                text = body.decode("utf-8", errors="replace")
                text = text.replace(f"{MTX_HLS_URL}/", "/live/")
                text = text.replace("http://127.0.0.1:8888/", "/live/")
                text = text.replace("http://localhost:8888/", "/live/")
                body = text.encode("utf-8")
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Access-Control-Allow-Origin": "*",
            }
            for key in ("Accept-Ranges", "Content-Range"):
                if resp.headers.get(key):
                    headers[key] = resp.headers[key]
            if request.method == "HEAD":
                return Response(content=b"", status_code=200, media_type=ctype, headers=headers)
            return Response(content=body, status_code=resp.status, media_type=ctype, headers=headers)
    except urllib.error.HTTPError as exc:
        return Response(content=exc.read() if request.method != "HEAD" else b"", status_code=exc.code)
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"UE MediaMTX HLS unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")


@app.api_route("/live/{path:path}", methods=["GET", "HEAD"])
def proxy_live(path: str, request: Request) -> Response:
    return _proxy_mtx_hls(path, request)


def _rewrite_whep_location(loc: str) -> str:
    loc = (loc or "").strip()
    for prefix in (MTX_WHEP_URL, "http://127.0.0.1:8889", "http://localhost:8889"):
        if loc.startswith(prefix):
            loc = loc[len(prefix) :] or "/"
            break
    if loc.startswith("/whep/"):
        return loc
    if not loc.startswith("/"):
        loc = "/" + loc
    return "/whep" + loc


@app.api_route("/whep/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
async def proxy_whep(path: str, request: Request) -> Response:
    cors = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": "Location, Link",
    }
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors)
    url = f"{MTX_WHEP_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()
    headers = {}
    for key in ("content-type", "accept", "if-match"):
        if key in request.headers:
            headers[key] = request.headers[key]
    req = urllib.request.Request(url, data=body or None, headers=headers, method=request.method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            out = dict(cors)
            loc = resp.headers.get("Location")
            if loc:
                out["Location"] = _rewrite_whep_location(loc)
            link = resp.headers.get("Link")
            if link:
                out["Link"] = link.replace(MTX_WHEP_URL, "/whep").replace(
                    "http://127.0.0.1:8889", "/whep"
                )
            ctype = resp.headers.get("Content-Type", "application/sdp")
            return Response(content=raw, status_code=resp.status, media_type=ctype, headers=out)
    except urllib.error.HTTPError as exc:
        return Response(content=exc.read(), status_code=exc.code, headers=cors)
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"UE MediaMTX WHEP unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json", headers=cors)


@app.api_route("/api/dl/{path:path}", methods=["GET", "HEAD"])
def proxy_dl(path: str, request: Request) -> Response:
    qs = str(request.query_params)
    tail = path + (("?" + qs) if qs else "")
    if path == "mjpeg" or path.startswith("hls"):
        return _stream_backend("dl/" + tail)
    return _forward("GET", "dl/" + tail, None)


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
