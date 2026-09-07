#!/usr/bin/env python3
"""Write dedicated frontend-console trees for every Exp4 app (server + s1/s4 client)."""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPS = HERE.parents[1]
LOGO_SRC = APPS / "exp4_s2_cctv" / "client" / "frontend-console" / "static" / "logos"

CSS = """
:root {
  --bg0:#070b14; --bg1:#0b1120; --card:rgba(15,23,42,.75); --surface:rgba(255,255,255,.035);
  --border:rgba(255,255,255,.08); --text:#eef2f9; --text-dim:#93a0b8; --text-faint:#5e6b85;
  --accent:#16E6A0; --accent2:#00C2D4; --bad:#ff5a6e; --ok:#16e6a0;
  --ok-bg:rgba(22,230,160,.12); --bad-bg:rgba(255,90,110,.12);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'IBM Plex Sans',system-ui,sans-serif;color:var(--text);min-height:100vh;
  background:radial-gradient(1200px 600px at 80% -10%,rgba(22,230,160,.10),transparent 60%),
             linear-gradient(180deg,var(--bg0),var(--bg1))}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
  padding:14px 24px;border-bottom:1px solid var(--border);background:rgba(7,11,20,.85);
  position:sticky;top:0;z-index:10}
h1{font-family:'Space Grotesk',sans-serif;font-size:20px}
.brand{display:flex;align-items:center;gap:12px}
.brand img{width:44px;height:auto;border-radius:8px}
.kicker{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent2);font-weight:600}
.pill{font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid var(--border);background:var(--surface);font-family:'IBM Plex Mono',monospace}
.pill.ok{color:var(--ok);border-color:rgba(22,230,160,.35);background:var(--ok-bg)}
.pill.bad{color:var(--bad);border-color:rgba(255,90,110,.35);background:var(--bad-bg)}
a.pill{text-decoration:none;color:var(--accent2);border-color:rgba(0,194,212,.35)}
a.pill:hover{background:rgba(0,194,212,.12)}
.hdr-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
main{padding:24px;max-width:1200px;margin:0 auto;display:grid;gap:16px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.kpi-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.kpi-label{font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.08em}
.kpi-value{font-size:15px;font-weight:600;margin-top:4px;word-break:break-all}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;display:grid;gap:12px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{background:var(--accent);color:#04140e;border:0;border-radius:8px;padding:9px 14px;font-weight:700;cursor:pointer}
button.secondary{background:var(--surface);color:var(--text);border:1px solid var(--border)}
button.danger{background:var(--bad-bg);color:var(--bad);border:1px solid rgba(255,90,110,.35)}
label{font-size:12px;color:var(--text-dim);display:block;margin-bottom:4px}
input,textarea,select{background:#05080f;border:1px solid var(--border);color:#fff;padding:8px 12px;border-radius:8px;font:inherit}
.log{background:#05080f;border:1px solid var(--border);border-radius:10px;padding:12px;font-family:'IBM Plex Mono',monospace;font-size:12px;max-height:420px;overflow:auto;white-space:pre-wrap}
pre{font-family:'IBM Plex Mono',monospace;font-size:12px;white-space:pre-wrap}
.card.term{padding:0;overflow:hidden}
.console{background:#05080f;overflow:hidden}
.console-bar{display:flex;align-items:center;gap:10px;padding:8px 12px;background:rgba(255,255,255,.04);border-bottom:1px solid var(--border);font-family:'IBM Plex Mono',monospace;font-size:11px}
.console-dots{display:flex;gap:5px}
.console-dots span{width:8px;height:8px;border-radius:50%;background:#3a455c}
.console-dots span:nth-child(1){background:#ff5a6e}
.console-dots span:nth-child(2){background:#f5a623}
.console-dots span:nth-child(3){background:#16e6a0}
.console-title{color:var(--text-dim);letter-spacing:.08em;text-transform:uppercase}
.console-meta{margin-left:auto;color:var(--text-faint)}
.console-body{font-family:'IBM Plex Mono',monospace;font-size:12px;line-height:1.5;min-height:280px;max-height:min(56vh,640px);overflow:auto;padding:10px 12px 14px;white-space:pre-wrap;word-break:break-word;color:#c6f6d5}
.console-idle{color:var(--text-faint)}
.console-line{padding:1px 0}
.console-line .ts{color:var(--text-faint);margin-right:8px}
.console-line.sum{color:var(--accent)}
.console-line.err{color:var(--bad)}
.console-line.sshd{color:var(--accent2)}
.table-wrap{overflow:auto}
table.clients{width:100%;border-collapse:collapse;font-size:13px}
table.clients th,table.clients td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
table.clients th{color:var(--text-faint);font-size:11px;text-transform:uppercase;letter-spacing:.08em;font-weight:600}
table.clients td.empty{color:var(--text-faint);text-align:center;padding:18px}
table.clients a{color:var(--accent2);text-decoration:none}
table.clients a:hover{text-decoration:underline}
.muted{color:var(--text-faint);font-size:12px;margin-top:2px}
"""


GRAFANA_BASE = "http://10.1.137.105:3000"


def grafana_dashboard_url(slice_id: int) -> str:
    uid = f"ina-exp4-s{slice_id}"
    return f"{GRAFANA_BASE}/d/{uid}/{uid}?orgId=1&refresh=5s"


def page(
    title: str,
    kicker: str,
    kpis: str,
    controls: str,
    poll_js: str,
    grafana_url: str = "",
    show_clients: bool = False,
) -> str:
    grafana_link = ""
    if grafana_url:
        grafana_link = (
            f'<a class="pill" href="{grafana_url}" target="_blank" rel="noopener">'
            "Grafana dashboard</a>"
        )
    clients_html = ""
    if show_clients:
        clients_html = """
    <section class="card">
      <h2>Connected clients</h2>
      <p class="kicker">UEs currently attached to this server. Multiple clients may share the same backend.</p>
      <div class="table-wrap">
        <table class="clients">
          <thead>
            <tr><th>Client</th><th>Console IP</th><th>Console</th><th>Status</th><th>Detail</th></tr>
          </thead>
          <tbody id="clients-body">
            <tr><td colspan="5" class="empty">waiting…</td></tr>
          </tbody>
        </table>
      </div>
    </section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{title}</title>
  <link rel="icon" type="image/png" href="/static/logos/NeuroRAN.png"/>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@600;700&display=swap" rel="stylesheet"/>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div class="brand">
      <img src="/static/logos/NeuroRAN.png" alt="NeuroRAN"/>
      <div>
        <div class="kicker">{kicker}</div>
        <h1>{title}</h1>
      </div>
    </div>
    <div class="hdr-actions">
      {grafana_link}
      <span id="pill" class="pill">connecting</span>
    </div>
  </header>
  <main>
    <section class="kpi" id="kpis">{kpis}</section>
    {clients_html}
    <section class="card">
      <h2>Control</h2>
      {controls}
      <div class="console" style="border:1px solid var(--border);border-radius:10px;margin-top:4px">
        <div class="console-bar">
          <span class="console-dots"><span></span><span></span><span></span></span>
          <span class="console-title" id="term-title">backend log</span>
          <span class="console-meta" id="term-meta">0 lines</span>
        </div>
        <div class="console-body" id="log"><div class="console-idle">waiting for backend…</div></div>
      </div>
      <details style="border-top:1px solid var(--border);padding-top:10px;margin-top:8px">
        <summary style="cursor:pointer;color:var(--text-dim);font-size:13px;font-weight:600">Backend status</summary>
        <pre id="raw" style="margin-top:10px">waiting…</pre>
      </details>
    </section>
  </main>
  <script>
    const logEl = document.getElementById('log');
    let lastSeq = 0;
    function esc(s) {{
      return String(s || '').replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
    }}
    function appendTerm(e) {{
      if (!e) return;
      const seq = Number(e.seq || 0);
      if (seq && seq <= lastSeq) return;
      const idle = logEl.querySelector('.console-idle');
      if (idle) idle.remove();
      const line = e.line || String(e);
      const div = document.createElement('div');
      div.className = 'console-line';
      const kind = String(e.kind || '');
      const low = line.toLowerCase();
      if (/error|fail|denied|refused/.test(low)) div.classList.add('err');
      else if (/\\[sum\\]|\\[ok\\]/i.test(line)) div.classList.add('sum');
      else if (kind === 'sshd') div.classList.add('sshd');
      div.innerHTML = '<span class="ts">' + esc(e.ts || '') + '</span>' + esc(line);
      logEl.appendChild(div);
      while (logEl.childElementCount > 500) logEl.removeChild(logEl.firstChild);
      logEl.scrollTop = logEl.scrollHeight;
      if (seq) lastSeq = seq;
      const meta = document.getElementById('term-meta');
      if (meta) meta.textContent = lastSeq + ' lines';
    }}
    function drainLogs(logs) {{ (logs || []).forEach(appendTerm); }}
    function log(msg) {{
      appendTerm({{seq: lastSeq + 1, ts: new Date().toISOString().slice(11,19), line: msg}});
    }}
    function clientStatus(c) {{
      if (c.connection_status) return String(c.connection_status);
      if (c.active === true) return 'Connected';
      if (c.active === false) return 'Stale';
      if (c.is_alive === true) return 'Connected';
      if (c.is_alive === false) return 'Disconnected';
      return 'Connected';
    }}
    function renderClients(s) {{
      const rows = s.clients || [];
      const kc = document.getElementById('k_clients');
      if (kc) kc.textContent = String(rows.length);
      const body = document.getElementById('clients-body');
      if (!body) return;
      if (!rows.length) {{
        body.innerHTML = '<tr><td colspan="5" class="empty">no clients connected</td></tr>';
        return;
      }}
      body.innerHTML = rows.map(c => {{
        const id = esc(c.id || c.client_id || '');
        const name = esc(c.name || '');
        const ip = esc(c.console_ip || c.ip || c.to_server_ip || '');
        const url = c.console_url || (ip ? ('http://' + ip) : '');
        const st = clientStatus(c);
        const extra = esc(
          c.detail || c.state ||
          (c.fps != null && c.fps !== '' ? (Number(c.fps).toFixed(1) + ' fps') : '') ||
          (c.last_heartbeat_ago != null ? (c.last_heartbeat_ago + 's ago') : '')
        );
        const stClass = /disc|stale|off/i.test(st) ? 'bad' : (/unstable/i.test(st) ? '' : 'ok');
        const link = url
          ? ('<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + '</a>')
          : '—';
        return '<tr><td><div>' + id + '</div>' +
          (name && name !== id ? '<div class="muted">' + name + '</div>' : '') +
          '</td><td>' + (ip || '—') + '</td><td>' + link +
          '</td><td><span class="pill ' + stClass + '">' + esc(st) + '</span></td><td>' +
          (extra || '—') + '</td></tr>';
      }}).join('');
    }}
    async function req(path, opts) {{
      const r = await fetch('/api/' + path, Object.assign({{headers:{{'Content-Type':'application/json'}}}}, opts||{{}}));
      const t = await r.text();
      let j; try {{ j = JSON.parse(t); }} catch {{ j = {{raw:t}}; }}
      if (!r.ok) throw new Error(j.detail || t || r.status);
      return j;
    }}
    {poll_js}
  </script>
</body>
</html>
"""


def kpi(kid: str, label: str) -> str:
    return f'<div class="kpi-card"><div class="kpi-label">{label}</div><div class="kpi-value" id="{kid}">—</div></div>'


SERVERS = (
    (
        "exp4_s1_iperf_sftp",
        "S1 Server Console: FTP",
        "",
        kpi("k_iperf", "iperf :5201") + kpi("k_sftp", "SFTP :22") + kpi("k_clients", "Clients") + kpi("k_n6", "N6"),
        """<div class="row">
          <button onclick="act('refresh')">Refresh</button>
        </div>
        <p class="kicker">iperf3 -s and sshd logs stream into the terminal below.</p>""",
        """
    document.getElementById('term-title').textContent = 'iperf3 -s / sshd';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.ok ? 'backend up' : 'backend down';
      document.getElementById('k_iperf').textContent = s.iperf_listen ? 'listen' : 'down';
      document.getElementById('k_sftp').textContent = s.sftp_listen ? 'listen' : 'down';
      document.getElementById('k_n6').textContent = s.n6_ip || '—';
      renderClients(s);
      const slim = Object.assign({}, s);
      delete slim.log;
      if (slim.iperf) { slim.iperf = Object.assign({}, slim.iperf); delete slim.iperf.log; }
      document.getElementById('raw').textContent = JSON.stringify(slim, null, 2);
      drainLogs(s.log || (s.iperf && s.iperf.log) || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function act() { await tick(); }
    tick(); setInterval(tick, 1000);
        """,
    ),
    (
        "exp4_s2_cctv",
        "S2 Server Console: CCTV / YOLO",
        "",
        kpi("k_yolo", "YOLO") + kpi("k_clients", "Clients") + kpi("k_mtx", "MediaMTX"),
        """<div class="row">
          <button onclick="act()">Refresh</button>
        </div>
        <p class="kicker">Proxies the CCTV FastAPI backend. Watch annotated streams and client health.</p>""",
        """
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok !== false ? 'ok' : 'bad');
      document.getElementById('pill').textContent = 'backend up';
      document.getElementById('k_yolo').textContent = s.yolo_enabled ? (s.yolo_model||'on') : 'off';
      document.getElementById('k_mtx').textContent = (s.mediamtx && !s.mediamtx.error) ? 'ok' : 'error';
      renderClients(s);
      document.getElementById('raw').textContent = JSON.stringify(s, null, 2);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function act() { await tick(); log('refreshed'); }
    tick(); setInterval(tick, 2000);
        """,
    ),
    (
        "exp4_s3_ott",
        "S3 Server Console: OTT",
        "",
        kpi("k_ch", "Channels") + kpi("k_ue", "UEs") + kpi("k_path", "Stream"),
        """<div class="row">
          <button onclick="act()">Refresh</button>
        </div>
        <p class="kicker">Proxies the OTT FastAPI backend (watch path, bitrate, attached UEs).</p>""",
        """
    function paint(s) {
      document.getElementById('pill').className = 'pill ok';
      document.getElementById('pill').textContent = 'backend up';
      document.getElementById('k_ch').textContent = s.channels_count ?? (s.channels||[]).length;
      document.getElementById('k_ue').textContent = s.clients_count ?? (s.clients||[]).length;
      document.getElementById('k_path').textContent = s.stream_path || (s.channels && s.channels[0] && s.channels[0].path) || '—';
      renderClients(s);
      document.getElementById('raw').textContent = JSON.stringify(s, null, 2);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function act() { await tick(); log('refreshed'); }
    tick(); setInterval(tick, 2000);
        """,
    ),
    (
        "exp4_s4_cpu_offload",
        "S4 Server Console: file encrypt",
        "",
        kpi("k_stream", "Stream") + kpi("k_clients", "Clients") + kpi("k_plain", "Generate q") + kpi("k_ready", "Encrypt q")
        + kpi("k_last", "Last encrypt") + kpi("k_dl", "Downloaded") + kpi("k_del", "Deleted"),
        """<div class="row">
          <button onclick="stream('start')">Start stream</button>
          <button class="danger" onclick="stream('stop')">Stop</button>
          <button class="secondary" onclick="runOnce()">Enqueue one</button>
        </div>
        <p class="kicker">Autostarts: generate → queue → encrypt → queue → download to UE → delete.</p>""",
        """
    document.getElementById('term-title').textContent = 'generate / encrypt / delete';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.stream_running ? 'streaming' : (s.ok ? 'idle' : 'down');
      const p = s.pipeline || {};
      const g = p.generate || {};
      const e = p.encrypt || {};
      document.getElementById('k_stream').textContent = s.stream_running ? 'running' : 'stopped';
      document.getElementById('k_plain').textContent = (g.q ?? '—') + ' / ' + (g.max ?? '—');
      document.getElementById('k_ready').textContent = (e.q ?? '—') + ' / ' + (e.max ?? '—');
      document.getElementById('k_last').textContent = (s.last_proc_ms!=null) ? (s.last_proc_ms.toFixed(1)+' ms') : '—';
      document.getElementById('k_dl').textContent = s.downloaded ?? 0;
      document.getElementById('k_del').textContent = s.deleted ?? 0;
      renderClients(s);
      const slim = Object.assign({}, s);
      delete slim.log;
      document.getElementById('raw').textContent = JSON.stringify(slim, null, 2);
      drainLogs(s.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function stream(action) {
      try { paint(await req('stream', {method:'POST', body: JSON.stringify({action})})); }
      catch(e) { log(e.message); }
    }
    async function runOnce() {
      try { const s = await req('run', {method:'POST', body:'{}'}); paint(s); log('enqueued '+ (s.file_id||'') + ' ' + s.last_proc_ms + ' ms'); }
      catch(e) { log(e.message); }
    }
    stream('start');
    tick(); setInterval(tick, 1000);
        """,
    ),
    (
        "exp4_s5_iot",
        "S5 Server Console: MQTT",
        "",
        kpi("k_br", "Broker") + kpi("k_dev", "Devices") + kpi("k_dl", "DL period"),
        """<div class="row">
          <input id="topic" value="slice_5/dl/ue1" style="min-width:220px"/>
          <input id="payload" value="ping" style="min-width:160px"/>
          <button onclick="pub()">Publish DL</button>
          <button class="secondary" onclick="act()">Refresh</button>
        </div>
        <p class="kicker">Mosquitto + downlink controller. Publish logs stream below.</p>""",
        """
    document.getElementById('term-title').textContent = 'MQTT log';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.ok ? 'backend up' : 'down';
      document.getElementById('k_br').textContent = s.broker_ok ? 'up' : 'down';
      document.getElementById('k_dev').textContent = s.device_count ?? (s.clients||[]).length;
      document.getElementById('k_dl').textContent = (s.dl_fast_period_s||'—') + ' s';
      renderClients(s);
      document.getElementById('raw').textContent = JSON.stringify(s, null, 2);
      drainLogs(s.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function act() { await tick(); }
    async function pub() {
      const body = JSON.stringify({topic: document.getElementById('topic').value, payload: document.getElementById('payload').value});
      try { await req('publish', {method:'POST', body}); log('published'); }
      catch(e) { log(e.message); }
    }
    tick(); setInterval(tick, 2000);
        """,
    ),
)

CLIENTS = (
    (
        "exp4_s1_iperf_sftp",
        1,
        "S1 Client Console: FTP",
        "",
        kpi("k_srv", "Server") + kpi("k_iperf", "iperf DL") + kpi("k_rate", "iperf rate") + kpi("k_last", "Last SFTP") + kpi("k_gp", "SFTP goodput"),
        """<div class="row">
          <button onclick="sftp()">SFTP download</button>
        </div>
        <p class="kicker">iperf3 -R -P 5 -b 10M -t 0 autostarts; stdout is streamed into the terminal.</p>""",
        """
    document.getElementById('term-title').textContent = 'iperf3 -R -P 5 -b 10M -t 0';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.ok ? 'backend up' : 'down';
      document.getElementById('k_srv').textContent = s.server || '—';
      const ip = s.iperf || {};
      document.getElementById('k_iperf').textContent = ip.running ? 'running' : (ip.error || 'stopped');
      document.getElementById('k_rate').textContent = (ip.mbits_per_second != null) ? (Number(ip.mbits_per_second).toFixed(2) + ' Mbit/s') : '—';
      document.getElementById('k_last').textContent = s.last && s.last.transfer_s ? (s.last.transfer_s.toFixed(3)+' s') : '—';
      document.getElementById('k_gp').textContent = s.last && s.last.goodput_mbit ? (s.last.goodput_mbit.toFixed(2)+' Mbit/s') : '—';
      const slim = Object.assign({}, s);
      delete slim.log;
      if (slim.iperf) { slim.iperf = Object.assign({}, slim.iperf); delete slim.iperf.log; }
      document.getElementById('raw').textContent = JSON.stringify(slim, null, 2);
      drainLogs(s.log || ip.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function sftp() { try { const s = await req('sftp', {method:'POST', body:'{}'}); paint(s); log('sftp done'); } catch(e) { log(e.message); } }
    tick(); setInterval(tick, 1000);
        """,
    ),
    (
        "exp4_s4_cpu_offload",
        4,
        "S4 Client Console: file encrypt",
        "",
        kpi("k_stream", "Stream") + kpi("k_ok", "Success") + kpi("k_file", "Last file")
        + kpi("k_bytes", "Last bytes") + kpi("k_dt", "Transfer") + kpi("k_del", "Deleted"),
        """<div class="row">
          <button onclick="stream('start')">Start stream</button>
          <button class="danger" onclick="stream('stop')">Stop</button>
          <button class="secondary" onclick="dl()">Download one</button>
        </div>
        <p class="kicker">Autostarts: UE pulls the next encrypted zip, shows success, then deletes the local copy.</p>""",
        """
    document.getElementById('term-title').textContent = 'download / success / delete';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.stream_running ? 'streaming' : (s.ok ? 'idle' : 'down');
      document.getElementById('k_stream').textContent = s.stream_running ? 'running' : 'stopped';
      document.getElementById('k_ok').textContent = s.success ?? 0;
      document.getElementById('k_file').textContent = (s.last && s.last.file_id) || '—';
      document.getElementById('k_bytes').textContent = (s.last && s.last.bytes) || '—';
      document.getElementById('k_dt').textContent = s.last && s.last.transfer_s ? (s.last.transfer_s.toFixed(3)+' s') : '—';
      document.getElementById('k_del').textContent = s.deleted ?? 0;
      const slim = Object.assign({}, s);
      delete slim.log;
      document.getElementById('raw').textContent = JSON.stringify(slim, null, 2);
      drainLogs(s.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function stream(action) {
      try { paint(await req('stream', {method:'POST', body: JSON.stringify({action})})); }
      catch(e) { log(e.message); }
    }
    async function dl() { try { const s = await req('download', {method:'POST', body:'{}'}); paint(s); } catch(e) { log(e.message); } }
    stream('start');
    tick(); setInterval(tick, 1000);
        """,
    ),
)


def write_console(
    dest: Path,
    title: str,
    kicker: str,
    kpis: str,
    controls: str,
    js: str,
    grafana_url: str = "",
    show_clients: bool = False,
) -> None:
    static = dest / "static"
    logos = static / "logos"
    dest.mkdir(parents=True, exist_ok=True)
    static.mkdir(parents=True, exist_ok=True)
    logos.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "frontend.py", dest / "frontend.py")
    (static / "index.html").write_text(
        page(
            title,
            kicker,
            kpis,
            controls,
            js,
            grafana_url=grafana_url,
            show_clients=show_clients,
        ),
        encoding="utf-8",
    )
    if LOGO_SRC.is_dir():
        for name in ("NeuroRAN.png", "NeuroRAN.svg"):
            src = LOGO_SRC / name
            if src.is_file():
                shutil.copy2(src, logos / name)
    print(f"wrote {dest}")


def main() -> None:
    for dirname, title, kicker, kpis, controls, js in SERVERS:
        write_console(
            APPS / dirname / "server" / "frontend-console",
            title,
            kicker,
            kpis,
            controls,
            js,
            show_clients=True,
        )
    for dirname, slice_id, title, kicker, kpis, controls, js in CLIENTS:
        write_console(
            APPS / dirname / "client" / "frontend-console",
            title,
            kicker,
            kpis,
            controls,
            js,
            grafana_url=grafana_dashboard_url(slice_id),
        )


if __name__ == "__main__":
    main()
