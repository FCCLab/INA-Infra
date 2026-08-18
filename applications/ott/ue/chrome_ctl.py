"""Control linuxserver/chromium via Chrome DevTools Protocol (CDP).

Chromium sidecar is started with:
  --remote-debugging-port=9222 --proxy-server=socks5://127.0.0.1:1080
Backend navigates tabs to YouTube; all browser egress goes through the PDU SOCKS proxy.
"""
from __future__ import annotations

import json
import logging
import os
import time
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
    timeout = max(1.0, float(timeout))
    deadline = time.monotonic() + timeout
    ws = websocket.create_connection(ws_url, timeout=min(timeout, 10.0))
    try:
        ws.send(json.dumps(payload))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP {method} timed out after {timeout:.0f}s")
            ws.settimeout(min(5.0, remaining))
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


def _cdp_click(ws_url: str, x: float, y: float) -> None:
    """Trusted mouse click (YouTube ignores untrusted element.click() on Skip)."""
    x, y = float(x), float(y)
    try:
        _ws_send(ws_url, "Page.bringToFront", timeout=2.0)
    except Exception:
        pass
    _ws_send(
        ws_url,
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": x, "y": y},
        timeout=3.0,
    )
    _ws_send(
        ws_url,
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
            "pointerType": "mouse",
        },
        timeout=3.0,
    )
    _ws_send(
        ws_url,
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
            "pointerType": "mouse",
        },
        timeout=3.0,
    )


# JS run in the YouTube page: find Skip, click it, optionally fast-forward the ad.
_SKIP_AD_JS = r"""
(function(){
  const out = {ad_detected:false, ad_skipped:false, ad_seeked:false, skip_rect:null};
  const player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
  if (player && (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting') || document.querySelector('.ytp-ad-player-overlay'))) {
    out.ad_detected = true;
  }
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const forceClick = (el) => {
    const target = el.closest ? (el.closest('button') || el) : el;
    const r = target.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const opts = {bubbles:true, cancelable:true, composed:true, view:window, clientX:x, clientY:y};
    try { target.focus(); } catch(e) {}
    try { target.dispatchEvent(new PointerEvent('pointerdown', Object.assign({pointerId:1, pointerType:'mouse', isPrimary:true, buttons:1}, opts))); } catch(e) {}
    try { target.dispatchEvent(new MouseEvent('mousedown', Object.assign({buttons:1}, opts))); } catch(e) {}
    try { target.dispatchEvent(new PointerEvent('pointerup', Object.assign({pointerId:1, pointerType:'mouse', isPrimary:true, buttons:0}, opts))); } catch(e) {}
    try { target.dispatchEvent(new MouseEvent('mouseup', opts)); } catch(e) {}
    try { target.dispatchEvent(new MouseEvent('click', opts)); } catch(e) {}
    try { target.click(); } catch(e) {}
    out.skip_rect = {x:x, y:y, w:r.width, h:r.height};
    return true;
  };
  const skipEls = [
    document.querySelector('button.ytp-skip-ad-button'),
    document.querySelector('.ytp-skip-ad-button'),
    document.querySelector('button.ytp-ad-skip-button-modern'),
    document.querySelector('.ytp-ad-skip-button-modern'),
    document.querySelector('button.ytp-ad-skip-button'),
    document.querySelector('.ytp-ad-skip-button'),
    document.querySelector('.ytp-skip-ad-button__text'),
    document.querySelector('.ytp-ad-skip-button-container button'),
    document.querySelector('.ytp-ad-skip-button-slot button'),
    document.querySelector('.ytp-ad-overlay-close-button')
  ];
  for (const el of skipEls) {
    if (visible(el)) {
      forceClick(el);
      out.ad_skipped = true;
      out.ad_detected = true;
      break;
    }
  }
  if (!out.ad_skipped) {
    const buttons = document.querySelectorAll('button, [role="button"]');
    for (const el of buttons) {
      const t = ((el.getAttribute && el.getAttribute('aria-label')) || el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
      if (!t || !/^skip(\s+ad)?$/i.test(t)) continue;
      if (visible(el)) {
        forceClick(el);
        out.ad_skipped = true;
        out.ad_detected = true;
        break;
      }
    }
  }
  if (out.ad_detected) {
    const v = document.querySelector('#movie_player video.html5-main-video, #movie_player video.video-stream, #movie_player video');
    if (v) {
      try { v.muted = true; v.volume = 0; } catch(e) {}
      try { v.playbackRate = 16; } catch(e) {}
      const dur = v.duration;
      if (isFinite(dur) && dur > 0.5 && dur < 900) {
        try { v.currentTime = Math.max(v.currentTime, dur - 0.12); out.ad_seeked = true; } catch(e) {}
      }
    }
  }
  return out;
})()
"""


def _pick_page(prefer_video_id: Optional[str] = None) -> Optional[dict]:
    pages = [t for t in list_targets() if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        return None

    def url_of(p: dict) -> str:
        return (p.get("url") or "").lower()

    needle = (prefer_video_id or "").strip().lower()
    if needle:
        for p in pages:
            u = url_of(p)
            if needle in u and "watch" in u:
                return p
    for p in pages:
        if "youtube.com/watch" in url_of(p):
            return p
    for p in pages:
        if "youtube" in url_of(p):
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


def _prefer_quality_list(yt_q: str) -> List[str]:
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
    seen = set()
    out: List[str] = []
    for q in prefer:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


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
      const pickVideo = () => document.querySelector(
        '#movie_player video.html5-main-video, video.html5-main-video, #movie_player video.video-stream, video.video-stream, #movie_player video'
      );
      for (let i = 0; i < 8; i++) {{
        const v = pickVideo();
        const player = document.getElementById('movie_player')
          || document.querySelector('.html5-video-player');
        try {{
          const skip = document.querySelector('button.ytp-skip-ad-button, .ytp-skip-ad-button, button.ytp-ad-skip-button-modern, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
          if (skip) skip.click();
          if (player && (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting'))) {{
            if (v) {{
              try {{ v.muted = true; v.playbackRate = 16; }} catch(e){{}}
              if (isFinite(v.duration) && v.duration > 0.5 && v.duration < 900) {{
                try {{ v.currentTime = Math.max(v.currentTime, v.duration - 0.12); }} catch(e){{}}
              }}
            }}
          }}
        }} catch(e) {{}}
        if (v) {{
          try {{ v.muted = true; v.volume = 0; }} catch(e){{}}
          if (v.paused || v.ended) {{
            try {{ await v.play(); }} catch(e){{}}
            const overlay = document.querySelector('button.ytp-large-play-button');
            if (v.paused && overlay && overlay.offsetParent !== null) {{
              try {{ overlay.click(); }} catch(e){{}}
            }}
          }}
        }}
        if (player) lastQ = applyQuality(player);
        if (v && !v.paused && !(player && (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting')))) {{
          lastQ = applyQuality(player || document.getElementById('movie_player'));
          return {{status: 'playing', quality: lastQ}};
        }}
        await sleep(250);
      }}
      return {{status: 'timeout', quality: lastQ}};
    }})()
    """
    # Give the watch page a moment to settle.
    import time as _time

    _time.sleep(0.4)
    result = _ws_send(
        ws_url,
        "Runtime.evaluate",
        {
            "expression": expr,
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
        },
        timeout=8.0,
    )
    return {"ok": True, "quality": yt_q, "result": result}


def check_and_heal_playback(
    quality: Optional[str] = None, video_id: Optional[str] = None
) -> Dict[str, Any]:
    """Check if YouTube is playing; resume if paused; skip ads. Never toggle pause."""
    page = _pick_page(prefer_video_id=video_id)
    if not page or not page.get("webSocketDebuggerUrl"):
        return {
            "ok": False,
            "detail": "no Chromium page",
            "page_loaded": False,
            "playing": False,
            "has_video": False,
            "url": "",
        }
    ws_url = page["webSocketDebuggerUrl"]
    yt_q = normalize_quality(quality)
    prefer_js = json.dumps(_prefer_quality_list(yt_q))
    target_js = json.dumps(yt_q)

    expr = r"""
    (function(){
      const href = String(window.location.href || '');
      const proto = String(window.location.protocol || '');
      let pageVideoId = '';
      try { pageVideoId = new URLSearchParams(window.location.search).get('v') || ''; } catch(e) {}
      const chromeError = proto === 'chrome-error:'
        || href.startsWith('chrome://')
        || href.startsWith('chrome-error://')
        || href.startsWith('about:')
        || href.startsWith('data:')
        || !href;
      const watch = /youtube\.com\/watch/i.test(href);
      const result = {
        playing: false,
        paused: true,
        ended: false,
        ad_detected: false,
        ad_skipped: false,
        ad_seeked: false,
        skip_rect: null,
        resumed: false,
        quality: null,
        error: null,
        url: href,
        page_url: href,
        page_video_id: pageVideoId,
        chrome_error: chromeError,
        doc_ready: document.readyState,
        page_loaded: false,
        has_video: false,
        has_player: false,
        ready_state: 0,
        current_time: 0,
        waiting: true
      };

      try {
        if (chromeError) {
          result.error = 'chrome_error_page';
          return result;
        }

        const adSkip = """ + _SKIP_AD_JS + r""";
        result.ad_detected = !!adSkip.ad_detected;
        result.ad_skipped = !!adSkip.ad_skipped;
        result.ad_seeked = !!adSkip.ad_seeked;
        result.skip_rect = adSkip.skip_rect;

        const v = document.querySelector(
          '#movie_player video.html5-main-video, video.html5-main-video, #movie_player video.video-stream, video.video-stream, #movie_player video'
        );
        const player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
        result.has_player = !!player;
        result.has_video = !!v;
        result.page_loaded = watch && document.readyState !== 'loading';

        const confirmButtons = document.querySelectorAll(
          'yt-confirm-dialog-renderer #confirm-button, paper-dialog #confirm-button, ytd-popup-container button#confirm-button'
        );
        confirmButtons.forEach(b => {
          try { b.click(); result.resumed = true; } catch(e) {}
        });

        if (v) {
          try { v.muted = true; v.volume = 0; } catch(e) {}
          result.ready_state = v.readyState || 0;
          result.current_time = v.currentTime || 0;
          result.paused = !!v.paused;
          result.ended = !!v.ended;
          result.waiting = v.readyState < 2;
          if (!result.ad_detected && (v.paused || v.ended)) {
            try {
              const p = v.play();
              if (p && p.catch) p.catch(function(){});
              result.resumed = true;
            } catch(e) {}
            const overlay = document.querySelector('button.ytp-large-play-button');
            if (v.paused && overlay && overlay.offsetParent !== null) {
              try { overlay.click(); result.resumed = true; } catch(e) {}
            }
          }
          result.playing = !v.paused && !v.ended;
          if (!result.ad_detected) {
            try { if (v.playbackRate !== 1) v.playbackRate = 1; } catch(e) {}
          }
        }
        if (!result.ad_detected && player && result.playing) {
          const target = """ + target_js + r""";
          const prefer = """ + prefer_js + r""";
          try {
            try {
              localStorage.setItem("yt-player-quality", JSON.stringify({
                data: target, expiration: Date.now() + 86400e3 * 30, creation: Date.now()
              }));
            } catch(e) {}
            try { localStorage.removeItem("yt-player-bandwidth"); } catch(e) {}
            const levels = (player.getAvailableQualityLevels && player.getAvailableQualityLevels()) || [];
            let pick = target;
            if (target !== "auto" && levels.length) {
              pick = prefer.find(q => q === "auto" || levels.includes(q)) || levels[0];
            }
            const cur = player.getPlaybackQuality ? player.getPlaybackQuality() : "";
            const h = v ? (v.videoHeight || 0) : 0;
            const wantUhd = (pick === "hd2160" || pick === "highres");
            const atTarget = (cur === pick) || (wantUhd && (cur === "hd2160" || cur === "highres"));
            const resOk = (target === "auto") || (wantUhd ? h >= 2000 : h >= 400);
            const good = atTarget && resOk;
            if (!good && levels.length) {
              try { player.setPlaybackQualityRange(pick, pick); } catch(e) {
                try { player.setPlaybackQualityRange(pick); } catch(e2) {}
              }
              try { player.setPlaybackQuality(pick); } catch(e) {}
            }
            result.quality = {
              applied: pick,
              current: player.getPlaybackQuality ? player.getPlaybackQuality() : cur,
              height: v ? (v.videoHeight || 0) : 0,
              width: v ? (v.videoWidth || 0) : 0,
              levels: levels,
              pinned: !good
            };
          } catch(e) {
            result.quality = {error: String(e)};
          }
        }
      } catch(err) {
        result.error = String(err);
      }

      return result;
    })()
    """
    try:
        res = _ws_send(
            ws_url,
            "Runtime.evaluate",
            {
                "expression": expr,
                "awaitPromise": False,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=8.0,
        )
        val = (res or {}).get("result", {}).get("value") or {}
        if not isinstance(val, dict):
            val = {"value": val}
        rect = val.get("skip_rect") or {}
        try:
            sx = float(rect.get("x") or 0)
            sy = float(rect.get("y") or 0)
            sw = float(rect.get("w") or 0)
            sh = float(rect.get("h") or 0)
            if sw > 2 and sh > 2:
                _cdp_click(ws_url, sx, sy)
                val["ad_skipped"] = True
                val["ad_detected"] = True
        except Exception:
            pass
        url = str(val.get("url") or val.get("page_url") or page.get("url") or "")
        val.setdefault("url", url)
        val.setdefault("page_url", url)
        if "page_loaded" not in val:
            val["page_loaded"] = "youtube.com/watch" in url.lower()
        return {"ok": True, **val}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "page_loaded": False,
            "playing": False,
            "has_video": False,
            "url": str((page or {}).get("url") or ""),
        }


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

