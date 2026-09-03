#!/usr/bin/env python3
"""OTT UE Frontend: NeuroRAN console (HTTPS) + same-origin Chromium (/chrome) proxy."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ott.ue.frontend")

STATIC_CANDIDATES = (
    os.environ.get("DASHBOARD_STATIC") or "",
    "/app/ue/static",
    "/app/static",
)


def _resolve_static_dir() -> Path:
    for raw in STATIC_CANDIDATES:
        if not raw:
            continue
        path = Path(raw)
        if (path / "index.html").is_file():
            return path
    return Path(os.environ.get("DASHBOARD_STATIC") or "/app/ue/static")


STATIC_DIR = _resolve_static_dir()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8090").rstrip("/")
CHROME_UPSTREAM = os.environ.get("CHROME_UPSTREAM", "http://127.0.0.1:3000").rstrip("/")
UE_NAME = os.environ.get("UE_NAME", "ott-ue")
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
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return Response(
            content=json.dumps({"ok": False, "detail": f"console UI missing at {index_path}"}).encode(),
            status_code=500,
            media_type="application/json",
        )
    return FileResponse(index_path)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "ue": UE_NAME, "console_ip": CONSOLE_IP, "console_mac": CONSOLE_MAC}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(path: str, request: Request) -> Response:
    data = None
    if request.method not in ("GET", "HEAD"):
        data = await request.body()
    return _forward(request.method, path, data)


@app.api_route("/live/{path:path}", methods=["GET", "HEAD"])
async def proxy_live(path: str, request: Request) -> Response:
    url = f"{BACKEND_URL}/live/{path}"
    headers = {}
    range_hdr = request.headers.get("range")
    if range_hdr and request.method != "HEAD":
        headers["Range"] = range_hdr
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            out_headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Access-Control-Allow-Origin": "*",
                "Content-Length": str(len(raw)),
            }
            for key in ("Accept-Ranges", "Content-Range"):
                if resp.headers.get(key):
                    out_headers[key] = resp.headers[key]
            if request.method == "HEAD":
                return Response(content=b"", status_code=200, media_type=ctype, headers=out_headers)
            return Response(content=raw, status_code=resp.status, media_type=ctype, headers=out_headers)
    except urllib.error.HTTPError as exc:
        return Response(content=exc.read() if request.method != "HEAD" else b"", status_code=exc.code)
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"live proxy unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")


def _chrome_upstream_url(path: str, query: str) -> str:
    # Chromium is configured with SUBFOLDER=/chrome/
    suffix = path.lstrip("/")
    base = f"{CHROME_UPSTREAM}/chrome/"
    if suffix:
        base = f"{CHROME_UPSTREAM}/chrome/{suffix}"
    if query:
        base = f"{base}?{query}"
    return base


@app.api_route("/chrome", methods=["GET", "HEAD", "POST", "OPTIONS"])
@app.api_route("/chrome/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
async def proxy_chrome(request: Request, path: str = "") -> Response:
    """Same-origin HTTPS reverse proxy → linuxserver/chromium (Selkies needs secure context)."""
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET,HEAD,POST,PUT,DELETE,OPTIONS,PATCH",
                "Access-Control-Allow-Headers": "*",
            },
        )
    url = _chrome_upstream_url(path, request.url.query)
    body = None
    if request.method not in ("GET", "HEAD"):
        body = await request.body()
    headers = {}
    for key in ("content-type", "accept", "accept-language", "range", "origin", "referer", "user-agent"):
        val = request.headers.get(key)
        if val:
            headers[key] = val
    headers["X-Forwarded-Proto"] = "https"
    headers["X-Forwarded-Host"] = request.headers.get("host") or CONSOLE_IP or "localhost"
    req = urllib.request.Request(
        url, data=body, headers=headers, method="GET" if request.method == "HEAD" else request.method
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        out_headers = {"Cache-Control": "no-store"}
        for key in ("Content-Type", "Content-Length", "Accept-Ranges", "Content-Range", "Location"):
            if resp.headers.get(key):
                out_headers[key] = resp.headers[key]
        status = getattr(resp, "status", 200) or 200
        if request.method == "HEAD":
            resp.close()
            return Response(content=b"", status_code=status, media_type=ctype, headers=out_headers)

        def _iter():
            try:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    resp.close()
                except Exception:
                    pass

        return StreamingResponse(_iter(), status_code=status, media_type=ctype, headers=out_headers)
    except urllib.error.HTTPError as exc:
        return Response(content=exc.read() if request.method != "HEAD" else b"", status_code=exc.code)
    except urllib.error.URLError as exc:
        payload = json.dumps({"ok": False, "detail": f"chromium upstream unreachable: {exc.reason}"})
        return Response(content=payload.encode(), status_code=503, media_type="application/json")


@app.websocket("/chrome/{path:path}")
@app.websocket("/chrome")
async def proxy_chrome_ws(websocket: WebSocket, path: str = ""):
    """WebSocket proxy for Selkies (must be WSS on the browser side).

    Connect upstream *before* accepting the browser socket so the client does not
    sit on "Waiting for server mode..." while Selkies is not yet attached, and so
    rapid accept/fail cycles do not trip Selkies reconnect rate-limiting.
    """
    q = websocket.scope.get("query_string", b"").decode()
    upstream = _chrome_upstream_url(path, q).replace("http://", "ws://").replace("https://", "wss://")
    try:
        import websockets  # type: ignore
    except ImportError:
        await websocket.close(code=1011, reason="websockets package missing")
        return

    up = None
    last_exc: Exception | None = None
    for attempt in range(1, 31):
        try:
            up = await websockets.connect(
                upstream,
                open_timeout=10,
                max_size=16 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            )
            break
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(1.0)
    if up is None:
        logger.warning("chrome ws upstream connect failed (%s): %s", upstream, last_exc)
        # Reject before accept → browser retries; avoid half-open "Waiting for server mode..."
        await websocket.close(code=1011, reason="chromium ws unreachable")
        return

    await websocket.accept()
    client_task = up_task = None
    try:

        async def client_to_up():
            try:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.receive":
                        if "bytes" in msg and msg["bytes"] is not None:
                            await up.send(msg["bytes"])
                        elif "text" in msg and msg["text"] is not None:
                            await up.send(msg["text"])
                    elif msg["type"] == "websocket.disconnect":
                        break
            except WebSocketDisconnect:
                pass
            finally:
                try:
                    await up.close()
                except Exception:
                    pass

        async def up_to_client():
            try:
                async for message in up:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            except Exception:
                pass
            finally:
                try:
                    await websocket.close()
                except Exception:
                    pass

        client_task = asyncio.create_task(client_to_up())
        up_task = asyncio.create_task(up_to_client())
        done, pending = await asyncio.wait(
            {client_task, up_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc:
                logger.debug("chrome ws side ended: %s", exc)
    except Exception as exc:
        logger.warning("chrome ws proxy error: %s", exc)
    finally:
        for task in (client_task, up_task):
            if task and not task.done():
                task.cancel()
        try:
            await up.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _run_http_redirect(port: int) -> None:
    """Port 80 → HTTPS so the console itself is a secure context."""
    from fastapi import FastAPI as _F

    redir = _F()

    @redir.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
    async def _r(request: Request, path: str = ""):
        host = request.headers.get("host") or CONSOLE_IP or "localhost"
        host = host.split(":")[0]
        suffix = path
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"https://{host}/{suffix}{q}", status_code=302)

    import uvicorn

    uvicorn.run(redir, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    import uvicorn

    http_port = int(os.environ.get("HTTP_PORT", os.environ.get("FRONTEND_PORT", "80")))
    https_port = int(os.environ.get("HTTPS_PORT", "443"))
    cert = os.environ.get("SSL_CERTFILE", "")
    key = os.environ.get("SSL_KEYFILE", "")
    if cert and key and Path(cert).is_file() and Path(key).is_file():
        threading.Thread(target=_run_http_redirect, args=(http_port,), name="http-redirect", daemon=True).start()
        logger.info("HTTPS console on :%s (cert=%s); HTTP :%s redirects", https_port, cert, http_port)
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=https_port,
            log_level="info",
            ssl_certfile=cert,
            ssl_keyfile=key,
        )
    else:
        logger.warning("No TLS certs — falling back to HTTP :%s (Selkies iframe will fail)", http_port)
        uvicorn.run(app, host="0.0.0.0", port=http_port, log_level="info")
