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
.term-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:900px){.term-grid{grid-template-columns:1fr}}
.iperf-ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 10px;border-bottom:1px solid var(--border);background:rgba(255,255,255,.025)}
.iperf-ctl button{padding:6px 12px;font-size:12px}
.iperf-ctl .iperf-args-label{flex:1 1 220px;min-width:180px;margin:0;display:flex;flex-direction:column;gap:3px;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-faint)}
.iperf-ctl input{width:100%;font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 8px;text-transform:none;letter-spacing:0;color:#eef2f9}
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
    dual_terms: bool = False,
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
    one_term = """
      <div class="console" style="border:1px solid var(--border);border-radius:10px;margin-top:4px">
        <div class="console-bar">
          <span class="console-dots"><span></span><span></span><span></span></span>
          <span class="console-title" id="term-title">backend log</span>
          <span class="console-meta" id="term-meta">0 lines</span>
        </div>
        <div class="console-body" id="log"><div class="console-idle">waiting for backend…</div></div>
      </div>"""
    two_terms = """
      <div class="term-grid" style="margin-top:4px">
        <div class="console" style="border:1px solid var(--border);border-radius:10px">
          <div class="console-bar">
            <span class="console-dots"><span></span><span></span><span></span></span>
            <span class="console-title" id="term-title">main application</span>
            <span class="console-meta" id="term-meta">0 lines</span>
          </div>
          <div class="console-body" id="log"><div class="console-idle">waiting for backend…</div></div>
        </div>
        <div class="console" style="border:1px solid var(--border);border-radius:10px">
          <div class="console-bar">
            <span class="console-dots"><span></span><span></span><span></span></span>
            <span class="console-title" id="iperf-term-title">iperf3</span>
            <span class="console-meta" id="iperf-term-meta">0 lines</span>
          </div>
          <div class="iperf-ctl">
            <button onclick="iperfAct('start')">Start</button>
            <button class="danger" onclick="iperfAct('stop')">Stop</button>
            <button class="secondary" onclick="iperfAct('update')">Update</button>
            <span id="iperf-pill" class="pill">iperf3 stopped</span>
            <label class="iperf-args-label">arguments
              <input id="iperf-args" type="text" spellcheck="false" autocomplete="off" placeholder="-c HOST -p 5201 -R -P 5 -b 10M -t 0 -i 1 --forceflush" onkeydown="if(event.key==='Enter'){event.preventDefault();iperfAct('start');}"/>
            </label>
          </div>
          <div class="console-body" id="iperf-log"><div class="console-idle">iperf3 stopped until started</div></div>
        </div>
      </div>"""
    term_html = two_terms if dual_terms else one_term
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
      {term_html}
      <details style="border-top:1px solid var(--border);padding-top:10px;margin-top:8px">
        <summary style="cursor:pointer;color:var(--text-dim);font-size:13px;font-weight:600">Backend status</summary>
        <pre id="raw" style="margin-top:10px">waiting…</pre>
      </details>
    </section>
  </main>
  <script>
    const logEl = document.getElementById('log');
    function esc(s) {{
      return String(s || '').replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
    }}
    function makeTerm(elId, metaId) {{
      const el = document.getElementById(elId);
      let lastSeq = 0;
      return function drain(logs) {{
        (logs || []).forEach(e => {{
          if (!e || !el) return;
          const seq = Number(e.seq || 0);
          if (seq && seq <= lastSeq) return;
          const idle = el.querySelector('.console-idle');
          if (idle) idle.remove();
          const line = e.line || e.msg || String(e);
          const div = document.createElement('div');
          div.className = 'console-line';
          const kind = String(e.kind || '');
          const low = line.toLowerCase();
          if (/error|fail|denied|refused/.test(low)) div.classList.add('err');
          else if (/\\[sum\\]|\\[ok\\]/i.test(line)) div.classList.add('sum');
          else if (kind === 'sshd') div.classList.add('sshd');
          div.innerHTML = '<span class="ts">' + esc(e.ts || e.time || '') + '</span>' + esc(line);
          el.appendChild(div);
          while (el.childElementCount > 500) el.removeChild(el.firstChild);
          el.scrollTop = el.scrollHeight;
          if (seq) lastSeq = seq;
          const meta = document.getElementById(metaId);
          if (meta) meta.textContent = lastSeq + ' lines';
        }});
      }};
    }}
    const drainApp = makeTerm('log', 'term-meta');
    const drainIperf = document.getElementById('iperf-log') ? makeTerm('iperf-log', 'iperf-term-meta') : function(){{}};
    function drainLogs(logs) {{ drainApp(logs); }}
    function log(msg) {{
      drainApp([{{ts: new Date().toISOString().slice(11,19), line: msg}}]);
    }}
    async function appAct(a) {{
      try {{ await req('app/' + a, {{method:'POST'}}); }}
      catch(e) {{ log(e.message); }}
    }}
    async function iperfAct(a) {{
      const argsEl = document.getElementById('iperf-args');
      const args = argsEl ? argsEl.value : '';
      try {{ await req('iperf', {{method:'POST', body: JSON.stringify({{action:a, args}})}}); }}
      catch(e) {{ log(e.message); }}
    }}
    function paintIperfPill(ip) {{
      const el = document.getElementById('iperf-pill');
      if (el) {{
        const running = !!(ip && ip.running);
        el.className = 'pill ' + (running ? 'ok' : '');
        el.textContent = running ? 'iperf3 running' : 'iperf3 stopped';
      }}
      const argsEl = document.getElementById('iperf-args');
      if (argsEl && document.activeElement !== argsEl && ip && ip.args != null) {{
        argsEl.value = ip.args;
      }}
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
        kpi("k_iperf", "iperf :5201") + kpi("k_sftp", "SFTP :22") + kpi("k_q", "FTP queue") + kpi("k_clients", "Clients") + kpi("k_n6", "N6"),
        """<div class="row">
          <button onclick="act('refresh')">Refresh</button>
        </div>
        <p class="kicker">Queues 1 MB files for SFTP. iperf3 -s and sshd logs stream below.</p>""",
        """
    document.getElementById('term-title').textContent = 'SFTP queue / iperf3 -s / sshd';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.ok ? 'backend up' : 'backend down';
      document.getElementById('k_iperf').textContent = s.iperf_listen ? 'listen' : 'down';
      document.getElementById('k_sftp').textContent = s.sftp_listen ? 'listen' : 'down';
      const q = s.sftp || {};
      document.getElementById('k_q').textContent = (q.ready_q ?? '—') + ' / ' + (q.ready_max ?? '—');
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
        "S4 Server Console: FTP + encrypt",
        "",
        kpi("k_iperf", "iperf :5201") + kpi("k_sftp", "SFTP :22") + kpi("k_q", "FTP queue")
        + kpi("k_enc", "Encrypt") + kpi("k_clients", "Clients") + kpi("k_n6", "N6"),
        """<div class="row">
          <button onclick="act('refresh')">Refresh</button>
        </div>
        <p class="kicker">Same 1 MB SFTP queue as S1, plus encrypt before the file is ready. iperf3 -s and sshd logs stream below.</p>""",
        """
    document.getElementById('term-title').textContent = 'generate+encrypt / SFTP queue / iperf3 -s / sshd';
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.ok ? 'backend up' : 'backend down';
      document.getElementById('k_iperf').textContent = s.iperf_listen ? 'listen' : 'down';
      document.getElementById('k_sftp').textContent = s.sftp_listen ? 'listen' : 'down';
      const q = s.sftp || {};
      document.getElementById('k_q').textContent = (q.ready_q ?? '—') + ' / ' + (q.ready_max ?? '—');
      document.getElementById('k_enc').textContent = (q.last_encrypt_ms != null) ? (Number(q.last_encrypt_ms).toFixed(1) + ' ms') : '—';
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
        "exp4_s5_iot",
        "S5 Server Console: MQTT",
        "",
        kpi("k_br", "Broker")
        + kpi("k_gen", "Generate")
        + kpi("k_dev", "Devices")
        + kpi("k_mps", "Target msgs/s")
        + kpi("k_pay", "Payload")
        + kpi("k_est", "Est. generate")
        + kpi("k_pub", "Publish rate")
        + kpi("k_pub_mbps", "Publish Mbps"),
        """<div class="row">
          <button onclick="dlAct('start')">Start generate</button>
          <button class="danger" onclick="dlAct('stop')">Stop generate</button>
          <label style="margin:0">Msgs/s per device
            <input id="mps" type="number" min="0" step="any" value="3000" style="width:110px;display:block"/>
          </label>
          <label style="margin:0">Payload bytes
            <input id="pay" type="number" min="64" step="1" value="128" style="width:110px;display:block"/>
          </label>
          <button class="secondary" onclick="dlApply()">Apply rate</button>
          <button class="secondary" onclick="act()">Refresh</button>
        </div>
        <div class="row">
          <input id="topic" value="slice_5/dl/ue1" style="min-width:220px"/>
          <input id="payload" value="ping" style="min-width:160px"/>
          <button onclick="pub()">Publish once</button>
        </div>
        <p class="kicker">Target is requested rate; Publish rate is measured at the server. High targets (e.g. 20k msg/s) are often limited by Python/paho/Mosquitto. Msgs/s = 0 means max rate. Est. Mbps = target × payload × 8 × devices / 1e6.</p>""",
        """
    document.getElementById('term-title').textContent = 'MQTT log';
    let seeded = false;
    function fmtEst(s) {
      if (!s.dl_running) return 'stopped';
      if (s.dl_est_mbps_total == null) return 'max (unbounded)';
      const per = s.dl_est_mbps_per_device != null ? Number(s.dl_est_mbps_per_device).toFixed(2) : '—';
      return Number(s.dl_est_mbps_total).toFixed(2) + ' Mbit/s (' + per + '/dev)';
    }
    function fmtPub(s) {
      const mps = s.dl_pub_msgs_per_s;
      if (mps == null) return '—';
      const err = Number(s.dl_pub_errors_per_s || 0);
      const base = Number(mps).toFixed(0) + ' msg/s';
      return err > 0.5 ? (base + ' (err ' + err.toFixed(0) + '/s)') : base;
    }
    function paint(s) {
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = s.dl_running ? 'generating' : (s.ok ? 'stopped' : 'down');
      document.getElementById('k_br').textContent = s.broker_ok ? 'up' : 'down';
      document.getElementById('k_gen').textContent = s.dl_running ? 'on' : 'off';
      document.getElementById('k_dev').textContent = s.device_count ?? (s.clients||[]).length;
      const mps = s.dl_msgs_per_s;
      document.getElementById('k_mps').textContent = (mps === 0 || mps === 0.0) ? 'max' : (mps != null ? mps : '—');
      document.getElementById('k_pay').textContent = s.dl_payload_bytes != null ? (s.dl_payload_bytes + ' B') : '—';
      document.getElementById('k_est').textContent = fmtEst(s);
      document.getElementById('k_pub').textContent = fmtPub(s);
      document.getElementById('k_pub_mbps').textContent =
        s.dl_pub_mbps != null ? (Number(s.dl_pub_mbps).toFixed(2) + ' Mbit/s') : '—';
      if (!seeded && s.dl_msgs_per_s != null) {
        document.getElementById('mps').value = s.dl_msgs_per_s;
        if (s.dl_payload_bytes != null) document.getElementById('pay').value = s.dl_payload_bytes;
        seeded = true;
      }
      renderClients(s);
      document.getElementById('raw').textContent = JSON.stringify(s, null, 2);
      drainLogs(s.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function act() { await tick(); }
    async function dlAct(which) {
      try { paint(await req('dl/' + which, {method:'POST', body:'{}'})); log(which === 'start' ? 'generate started' : 'generate stopped'); }
      catch(e) { log(e.message); }
    }
    async function dlApply() {
      const body = JSON.stringify({
        msgs_per_s: Number(document.getElementById('mps').value),
        payload_bytes: Number(document.getElementById('pay').value)
      });
      try { paint(await req('dl/config', {method:'POST', body})); log('rate applied'); }
      catch(e) { log(e.message); }
    }
    async function pub() {
      const body = JSON.stringify({topic: document.getElementById('topic').value, payload: document.getElementById('payload').value});
      try { await req('publish', {method:'POST', body}); log('published once'); }
      catch(e) { log(e.message); }
    }
    tick(); setInterval(tick, 1000);
        """,
    ),
)

CLIENTS = (
    (
        "exp4_s1_iperf_sftp",
        1,
        "S1 Client Console: FTP",
        "",
        kpi("k_srv", "Server") + kpi("k_iperf", "iperf DL") + kpi("k_e2e", "SFTP e2e") + kpi("k_last", "Last SFTP") + kpi("k_gp", "SFTP goodput"),
        """<div class="row">
          <button onclick="appAct('start')">Start SFTP</button>
          <button class="danger" onclick="appAct('stop')">Stop SFTP</button>
          <button class="secondary" onclick="sftp()">SFTP one</button>
        </div>
        <p class="kicker">SFTP queue and extra iperf3 DL (-R) are independent. Use the iperf3 terminal to start, stop, or update arguments.</p>""",
        """
    document.getElementById('term-title').textContent = 'SFTP 1 MB queue';
    function paint(s) {
      const ap = s.app || {};
      const ip = s.iperf || {};
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = ap.running ? 'sftp streaming' : (s.ok ? 'backend up' : 'down');
      document.getElementById('k_srv').textContent = s.server || '—';
      document.getElementById('k_iperf').textContent = ip.running ? 'running' : (ip.error || 'stopped');
      document.getElementById('k_e2e').textContent = s.last && s.last.e2e_ms != null ? (Number(s.last.e2e_ms).toFixed(1) + ' ms') : '—';
      document.getElementById('k_last').textContent = s.last && s.last.transfer_s ? (s.last.transfer_s.toFixed(3)+' s') : '—';
      document.getElementById('k_gp').textContent = s.last && s.last.goodput_mbit ? (s.last.goodput_mbit.toFixed(2)+' Mbit/s') : '—';
      paintIperfPill(ip);
      const slim = Object.assign({}, s);
      delete slim.log;
      if (slim.app) { slim.app = Object.assign({}, slim.app); delete slim.app.log; }
      if (slim.iperf) { slim.iperf = Object.assign({}, slim.iperf); delete slim.iperf.log; }
      document.getElementById('raw').textContent = JSON.stringify(slim, null, 2);
      drainApp(ap.log || s.log || []);
      drainIperf(ip.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function sftp() { try { const s = await req('sftp', {method:'POST', body:'{}'}); paint(s); log('sftp done'); } catch(e) { log(e.message); } }
    tick(); setInterval(tick, 1000);
        """,
    ),
    (
        "exp4_s4_cpu_offload",
        4,
        "S4 Client Console: FTP + encrypt",
        "",
        kpi("k_srv", "Server") + kpi("k_iperf", "iperf DL") + kpi("k_e2e", "SFTP e2e") + kpi("k_last", "Last SFTP") + kpi("k_gp", "SFTP goodput"),
        """<div class="row">
          <button onclick="appAct('start')">Start SFTP</button>
          <button class="danger" onclick="appAct('stop')">Stop SFTP</button>
          <button class="secondary" onclick="sftp()">SFTP one</button>
        </div>
        <p class="kicker">Same SFTP 1 MB queue as S1 after server-side encrypt. Use the iperf3 terminal to start, stop, or update arguments.</p>""",
        """
    document.getElementById('term-title').textContent = 'SFTP encrypted 1 MB';
    function paint(s) {
      const ap = s.app || {};
      const ip = s.iperf || {};
      document.getElementById('pill').className = 'pill ' + (s.ok ? 'ok' : 'bad');
      document.getElementById('pill').textContent = ap.running ? 'sftp streaming' : (s.ok ? 'backend up' : 'down');
      document.getElementById('k_srv').textContent = s.server || '—';
      document.getElementById('k_iperf').textContent = ip.running ? 'running' : (ip.error || 'stopped');
      document.getElementById('k_e2e').textContent = s.last && s.last.e2e_ms != null ? (Number(s.last.e2e_ms).toFixed(1) + ' ms') : '—';
      document.getElementById('k_last').textContent = s.last && s.last.transfer_s ? (s.last.transfer_s.toFixed(3)+' s') : '—';
      document.getElementById('k_gp').textContent = s.last && s.last.goodput_mbit ? (s.last.goodput_mbit.toFixed(2)+' Mbit/s') : '—';
      paintIperfPill(ip);
      const slim = Object.assign({}, s);
      delete slim.log;
      if (slim.app) { slim.app = Object.assign({}, slim.app); delete slim.app.log; }
      if (slim.iperf) { slim.iperf = Object.assign({}, slim.iperf); delete slim.iperf.log; }
      document.getElementById('raw').textContent = JSON.stringify(slim, null, 2);
      drainApp(ap.log || s.log || []);
      drainIperf(ip.log || []);
    }
    async function tick() { try { paint(await req('status')); } catch(e) { document.getElementById('pill').className='pill bad'; log(e.message); } }
    async function sftp() { try { const s = await req('sftp', {method:'POST', body:'{}'}); paint(s); log('sftp done'); } catch(e) { log(e.message); } }
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
    dual_terms: bool = False,
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
            dual_terms=dual_terms,
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
            dual_terms=True,
        )


if __name__ == "__main__":
    main()
