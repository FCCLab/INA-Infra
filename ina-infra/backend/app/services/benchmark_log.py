"""Append-only file log for oai-benchmark deploy/undeploy + CPU sweep.

Default path: ``<ina-infra>/logs/benchmark.log`` (override with
``INA_BENCHMARK_LOG``). UI/SSE streams are unchanged.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.services import paths as ina_paths

_lock = threading.Lock()


def log_path() -> Path:
    env = (os.environ.get("INA_BENCHMARK_LOG") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return ina_paths.ina_infra_root() / "logs" / "benchmark.log"


def write(line: str, *, source: str = "run") -> None:
    text = (line or "").rstrip("\n")
    if not text:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    row = f"{ts} [{source}] {text}\n"
    path = log_path()
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(row)
                fh.flush()
    except OSError:
        pass


def tee_sse(source: str, events: Iterator[str]) -> Iterator[str]:
    """Yield SSE chunks and mirror status/log/error/result into the file."""
    for chunk in events:
        _log_sse_chunk(source, chunk)
        yield chunk


def _log_sse_chunk(source: str, chunk: str) -> None:
    event = ""
    data_lines: list[str] = []
    for line in (chunk or "").splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return
    raw = "\n".join(data_lines)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        write(raw, source=source)
        return
    if not isinstance(data, dict):
        write(raw, source=source)
        return
    if event == "log":
        stream = str(data.get("stream") or "stdout")
        line = str(data.get("line") or "")
        write(f"[{stream}] {line}" if stream != "stdout" else line, source=source)
        return
    if event == "status":
        write(str(data.get("message") or data), source=source)
        return
    if event == "error":
        write(f"ERROR {data.get('message') or data}", source=source)
        return
    if event == "result":
        msg = data.get("message") or ""
        write(f"result ok={data.get('ok')} {msg}".rstrip(), source=source)
        return
    write(raw, source=source)
