"""Control linuxserver/chromium via Chrome DevTools Protocol (CDP).

Chromium sidecar is started with:
  --remote-debugging-port=9222 --proxy-server=socks5://127.0.0.1:1080
Backend navigates tabs to YouTube; all browser egress goes through the PDU SOCKS proxy.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ott.ue.chrome_ctl")

CDP_HOST = os.environ.get("CHROME_CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CHROME_CDP_PORT", "9222"))
CHROME_HTTP = os.environ.get("CHROME_HTTP_URL", "https://127.0.0.1/chrome/").rstrip("/")


def _cdp_base() -> str:
    return f"http://{CDP_HOST}:{CDP_PORT}"


def cdp_ready(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{_cdp_base()}/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_targets() -> List[dict]:
    try:
        with urllib.request.urlopen(f"{_cdp_base()}/json/list", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data if isinstance(data, list) else []
    except Exception as exc:
        logger.debug("cdp list failed: %s", exc)
        return []


def _ws_send(ws_url: str, method: str, params: Optional[dict] = None, timeout: float = 15.0) -> dict:
    try:
        import websocket  # type: ignore
    except ImportError as exc:
        raise RuntimeError("websocket-client not installed") from exc

    payload = {"id": 1, "method": method, "params": params or {}}
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        ws.send(json.dumps(payload))
        while True:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result") or {}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _pick_page() -> Optional[dict]:
    pages = [t for t in list_targets() if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        return None
    # Prefer a YouTube tab if present.
    for p in pages:
        u = (p.get("url") or "").lower()
        if "youtube" in u:
            return p
    return pages[0]


def navigate(url: str) -> Dict[str, Any]:
    """Navigate Chromium to url via CDP (creates a tab if needed)."""
    if not cdp_ready():
        raise RuntimeError(f"Chromium CDP not ready at {_cdp_base()}")

    page = _pick_page()
    if not page:
        # Ask Chrome to open a new tab (supported on many builds).
        enc = urllib.parse.quote(url, safe="")
        try:
            with urllib.request.urlopen(f"{_cdp_base()}/json/new?{enc}", timeout=10) as resp:
                page = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Some builds require PUT.
            req = urllib.request.Request(f"{_cdp_base()}/json/new?{enc}", method="PUT")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    page = json.loads(resp.read().decode())
            except Exception as exc2:
                raise RuntimeError(f"cannot open Chromium tab: {exc}; {exc2}") from exc2

    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("no webSocketDebuggerUrl for Chromium page")

    _ws_send(ws_url, "Page.enable")
    _ws_send(ws_url, "Network.enable")
    # Ensure Referer/Origin reach YouTube (Error 153 if stripped).
    try:
        _ws_send(
            ws_url,
            "Network.setExtraHTTPHeaders",
            {
                "headers": {
                    "Referer": "https://www.youtube.com/",
                    "Origin": "https://www.youtube.com",
                }
            },
        )
    except Exception:
        pass
    _ws_send(ws_url, "Page.navigate", {"url": url})
    return {
        "ok": True,
        "url": url,
        "target_id": page.get("id"),
        "title": page.get("title"),
        "chrome_ui": CHROME_HTTP,
        "ws_url": ws_url,
    }


QUALITY_ALIASES = {
    "auto": "auto",
    "default": "auto",
    "144p": "tiny",
    "240p": "small",
    "360p": "medium",
    "480p": "large",
    "720p": "hd720",
    "1080p": "hd1080",
    "1440p": "hd1440",
    "2k": "hd1440",
    "2160p": "hd2160",
    "4k": "hd2160",
    "uhd": "hd2160",
    "4320p": "highres",
    "8k": "highres",
    "highres": "highres",
    "hd2160": "hd2160",
    "hd1440": "hd1440",
    "hd1080": "hd1080",
    "hd720": "hd720",
}


def normalize_quality(quality: Optional[str]) -> str:
    """Map UI labels (4k, 1080p, …) to YouTube player quality codes. Default hd2160."""
    raw = (quality or os.environ.get("OTT_PLAY_QUALITY") or "4k").strip().lower()
    return QUALITY_ALIASES.get(raw, raw if raw in QUALITY_ALIASES.values() else "hd2160")


def youtube_vq_param(yt_quality: str) -> str:
    """YouTube watch URL vq= hint (best-effort; CDP setPlaybackQuality is authoritative)."""
    if yt_quality in ("auto",):
        return ""
    return yt_quality


def play_youtube(quality: Optional[str] = None) -> Dict[str, Any]:
    """After watch-page load: mute, play, force preferred quality (default 4K / hd2160)."""
    page = _pick_page()
    if not page or not page.get("webSocketDebuggerUrl"):
        raise RuntimeError("no Chromium page for play_youtube")
    ws_url = page["webSocketDebuggerUrl"]
    yt_q = normalize_quality(quality)
    # Prefer exact quality; fall back down the ladder if unavailable.
    prefer = [
        yt_q,
        "hd2160",
        "highres",
        "hd1440",
        "hd1080",
        "hd720",
        "large",
        "medium",
        "auto",
    ]
    # Dedupe while preserving order.
    seen = set()
    prefer_list = []
    for q in prefer:
        if q and q not in seen:
            seen.add(q)
            prefer_list.append(q)
    prefer_js = json.dumps(prefer_list)
    target_js = json.dumps(yt_q)
    expr = f"""
    (async function(){{
      const sleep = (ms) => new Promise(r => setTimeout(r, ms));
      const prefer = {prefer_js};
      const target = {target_js};
      const applyQuality = (player) => {{
        if (!player) return 'no-player';
        try {{
          const levels = (player.getAvailableQualityLevels && player.getAvailableQualityLevels()) || [];
          let pick = target;
          if (target !== 'auto' && levels.length) {{
            pick = prefer.find(q => q === 'auto' || levels.includes(q)) || levels[0];
          }}
          if (player.setPlaybackQualityRange) {{
            try {{ player.setPlaybackQualityRange(pick, pick); }} catch(e) {{
              try {{ player.setPlaybackQualityRange(pick); }} catch(e2) {{}}
            }}
          }}
          if (player.setPlaybackQuality) {{
            try {{ player.setPlaybackQuality(pick); }} catch(e) {{}}
          }}
          const cur = player.getPlaybackQuality ? player.getPlaybackQuality() : pick;
          return {{applied: pick, current: cur, levels: levels}};
        }} catch(e) {{
          return {{error: String(e)}};
        }}
      }};
      let lastQ = null;
      for (let i = 0; i < 40; i++) {{
        const v = document.querySelector('video');
        const player = document.getElementById('movie_player')
          || document.querySelector('.html5-video-player');
        const btn = document.querySelector(
          'button.ytp-large-play-button, button[aria-label^="Play"], .ytp-play-button'
        );
        if (v) {{
          try {{ v.muted = true; v.volume = 0; }} catch(e){{}}
          try {{ await v.play(); }} catch(e){{}}
        }}
        if (btn) {{ try {{ btn.click(); }} catch(e){{}} }}
        if (player) lastQ = applyQuality(player);
        if (v && !v.paused) {{
          // Re-assert quality a few times after play starts (YT often resets to auto).
          for (let j = 0; j < 6; j++) {{
            await sleep(800);
            lastQ = applyQuality(player || document.getElementById('movie_player'));
          }}
          return {{status: 'playing', quality: lastQ}};
        }}
        await sleep(500);
      }}
      return {{status: 'timeout', quality: lastQ}};
    }})()
    """
    # Give the watch page a moment to settle.
    import time as _time

    _time.sleep(2.0)
    result = _ws_send(
        ws_url,
        "Runtime.evaluate",
        {"expression": expr, "awaitPromise": True, "returnByValue": True},
        timeout=45.0,
    )
    return {"ok": True, "quality": yt_q, "result": result}


def blank() -> Dict[str, Any]:
    return navigate("about:blank")


def status() -> Dict[str, Any]:
    ready = cdp_ready()
    pages = list_targets() if ready else []
    page = None
    for p in pages:
        if p.get("type") == "page":
            page = p
            if "youtube" in (p.get("url") or "").lower():
                break
    return {
        "cdp_ready": ready,
        "cdp": _cdp_base(),
        "chrome_ui": CHROME_HTTP,
        "pages": len(pages),
        "url": (page or {}).get("url") or "",
        "title": (page or {}).get("title") or "",
    }
