"""Helpers for streaming subprocess output as Server-Sent Events."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


def sse(event: str, data: Any) -> str:
    """Format one SSE message (event + JSON data)."""
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    # SSE: each data line is one logical line; keep payload on a single line.
    payload = payload.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in payload:
        lines = "\n".join(f"data: {ln}" for ln in payload.split("\n"))
        return f"event: {event}\n{lines}\n\n"
    return f"event: {event}\ndata: {payload}\n\n"


def kill_process_group(proc: subprocess.Popen[Any]) -> None:
    """SIGTERM then SIGKILL the whole session (bash + nested ssh/kubectl)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def stream_cmd(
    cmd: Sequence[str],
    *,
    cwd: str,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[tuple[str, str] | CmdResult]:
    """Yield ``(\"stdout\"|\"stderr\", line)`` then a final ``CmdResult``.

    Lines are stripped of trailing newlines. On timeout the process is killed
    and a ``CmdResult`` with returncode 124 is yielded. If ``cancel_event`` is
    set, the process group is killed and returncode 130 is yielded.
    """
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None

    q: queue.Queue[tuple[str, Optional[str]]] = queue.Queue()
    out_buf: List[str] = []
    err_buf: List[str] = []

    def _reader(stream: Any, name: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                q.put((name, line.rstrip("\n")))
        finally:
            q.put((name, None))

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for t in threads:
        t.start()

    done = 0
    deadline = time.monotonic() + timeout if timeout is not None else None
    timed_out = False
    cancelled = False

    def _drain_remaining() -> Iterator[tuple[str, str]]:
        nonlocal done
        while done < 2:
            try:
                name, line = q.get(timeout=0.2)
            except queue.Empty:
                break
            if line is None:
                done += 1
                continue
            if name == "stdout":
                out_buf.append(line)
            else:
                err_buf.append(line)
            yield (name, line)

    while done < 2:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        remaining = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
        try:
            name, line = q.get(timeout=0.5 if remaining is None else min(0.5, remaining))
        except queue.Empty:
            if proc.poll() is not None and done < 2:
                # Process exited; wait briefly for readers to finish.
                continue
            continue
        if line is None:
            done += 1
            continue
        if name == "stdout":
            out_buf.append(line)
        else:
            err_buf.append(line)
        yield (name, line)

    if timed_out or cancelled:
        kill_process_group(proc)
        for item in _drain_remaining():
            yield item
        reason = (
            "command cancelled"
            if cancelled
            else f"command timed out after {timeout}s"
        )
        yield CmdResult(
            returncode=130 if cancelled else 124,
            stdout="\n".join(out_buf),
            stderr="\n".join(err_buf) + ("\n" if err_buf else "") + reason,
        )
        return

    rc = proc.wait()
    for t in threads:
        t.join(timeout=2)
    yield CmdResult(
        returncode=rc,
        stdout="\n".join(out_buf),
        stderr="\n".join(err_buf),
    )


def log_event(stream: str, line: str) -> str:
    return sse("log", {"stream": stream, "line": line})


def status_event(message: str, **extra: Any) -> str:
    payload: Dict[str, Any] = {"message": message}
    payload.update(extra)
    return sse("status", payload)


def result_event(data: Any) -> str:
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    return sse("result", data)


def error_event(message: str) -> str:
    return sse("error", {"message": message})
