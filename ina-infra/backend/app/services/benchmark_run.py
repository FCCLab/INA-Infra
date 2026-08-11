"""CPU-sweep runner for oai-benchmark: apply CPU → warmup → measure window.

Throughput is sampled into SQLite at step stop; the UI only shows start/stop.
The Operators agent does not control SCHED_RR — this path only sets CPU qty.
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

from app.schemas import (
    BenchmarkRunRequest,
    BenchmarkRunStatusOut,
    BenchmarkRunStopResponse,
    OperatorResourceSetRequest,
)
from app.services import benchmark_log, benchmark_store, operators

_lock = threading.Lock()
_cancel = threading.Event()
_thread: Optional[threading.Thread] = None
_active_id: Optional[int] = None

APPLY_TIMEOUT_SEC = 90.0
DEFAULT_OPERATOR_NS = "oai-benchmark"


def parse_cpu_millis(qty: str) -> int:
    s = (qty or "").strip().lower()
    if not s:
        raise ValueError("empty CPU quantity")
    if s.endswith("m"):
        return int(round(float(s[:-1])))
    return int(round(float(s) * 1000))


def format_cpu_millis(millis: int) -> str:
    if millis < 0:
        raise ValueError("CPU millis must be >= 0")
    # Keep millicores so 1000m stays on the 50m ladder (not rewritten as "1").
    return f"{millis}m"


def cpu_steps(min_cpu: str, max_cpu: str, cpu_step: str) -> List[str]:
    lo = parse_cpu_millis(min_cpu)
    hi = parse_cpu_millis(max_cpu)
    inc = parse_cpu_millis(cpu_step)
    if hi < lo:
        raise ValueError(f"max_cpu {max_cpu!r} < min_cpu {min_cpu!r}")
    if inc <= 0:
        raise ValueError("cpu_step must be > 0")
    out: List[str] = []
    x = lo
    while x <= hi:
        out.append(format_cpu_millis(x))
        x += inc
    if not out or parse_cpu_millis(out[-1]) != hi:
        out.append(format_cpu_millis(hi))
    return out


def sample_throughput_mbps() -> Optional[float]:
    """Hook: persist iperf/Influx sample on step stop. None until wired."""
    return None


def _interruptible_sleep(seconds: float, cancel: threading.Event) -> bool:
    """Sleep ``seconds``. Return True if finished, False if cancelled."""
    if seconds <= 0:
        return not cancel.is_set()
    deadline = time.monotonic() + seconds
    while True:
        if cancel.is_set():
            return False
        left = deadline - time.monotonic()
        if left <= 0:
            return True
        cancel.wait(min(0.2, left))


def _try_resolve_operator(operator_id: str, nf: str) -> str:
    try:
        op = operators.get_operator(operator_id)
        if op.online and operators.has_ws(operator_id):
            names = {n.name for n in op.nfs}
            if nf in names:
                return operator_id
            raise ValueError(f"operator {operator_id!r} has no NF {nf!r}")
    except KeyError:
        pass
    listed = operators.list_operators()
    for op in listed.operators:
        if not op.online or not operators.has_ws(op.id):
            continue
        if (op.namespace or "") != DEFAULT_OPERATOR_NS and op.id != operator_id:
            continue
        if any(n.name == nf for n in op.nfs):
            return op.id
    raise ValueError(
        f"no WebSocket-connected operator for NF {nf!r} "
        f"(wanted {operator_id!r} in {DEFAULT_OPERATOR_NS})"
    )


def _resolve_operator(operator_id: str, nf: str, *, wait_sec: float = 90.0) -> str:
    """Wait for the RAN agent WS (empty registry right after uvicorn --reload)."""
    deadline = time.monotonic() + wait_sec
    last = ""
    while True:
        try:
            return _try_resolve_operator(operator_id, nf)
        except ValueError as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            raise ValueError(
                f"{last}. Operators tab should show {operator_id} online — "
                "the agent reconnects after an API reload."
            )
        benchmark_log.write(
            f"waiting for operator {operator_id} nf={nf}: {last}",
            source="sweep",
        )
        time.sleep(1.0)


def wait_cpu_apply(
    operator_id: str,
    nf: str,
    generation: int,
    *,
    timeout: float = APPLY_TIMEOUT_SEC,
    cancel: Optional[threading.Event] = None,
) -> Tuple[bool, str]:
    deadline = time.monotonic() + timeout
    ev = cancel or threading.Event()
    last = "waiting for apply"
    last_push = 0.0
    benchmark_log.write(
        f"wait apply {operator_id}/{nf} gen={generation} timeout={timeout:.0f}s",
        source="sweep",
    )
    while time.monotonic() < deadline:
        if ev.is_set():
            return False, "cancelled"
        now = time.monotonic()
        if now - last_push >= 2.0:
            operators.push_desired(operator_id)
            last_push = now
        try:
            op = operators.get_operator(operator_id)
        except KeyError:
            last = "operator disconnected"
            ev.wait(0.25)
            continue
        row = next((n for n in op.nfs if n.name == nf), None)
        if row is None:
            last = f"NF {nf} missing"
        elif row.apply_status == "ok" and int(row.applied_generation or 0) >= generation:
            benchmark_log.write(
                f"apply ok {operator_id}/{nf} gen={generation}",
                source="sweep",
            )
            return True, ""
        elif row.apply_status == "error" and int(row.applied_generation or 0) >= generation:
            err = row.apply_message or "apply error"
            benchmark_log.write(
                f"apply error {operator_id}/{nf} gen={generation} {err}",
                source="sweep",
            )
            return False, err
        else:
            ws = "ws" if operators.has_ws(operator_id) else "no-ws"
            last = f"{row.apply_status or 'pending'} ({ws})"
        ev.wait(0.25)
    err = f"apply timeout ({last})"
    benchmark_log.write(f"apply timeout {operator_id}/{nf} gen={generation} {last}", source="sweep")
    return False, err


def _worker(run_id: int, req: BenchmarkRunRequest, cpus: List[str], oid: str) -> None:
    try:
        for i, cpu in enumerate(cpus):
            if _cancel.is_set():
                benchmark_log.write(
                    f"run {run_id} step {i + 1}/{len(cpus)} {cpu} cancelled",
                    source="sweep",
                )
                benchmark_store.update_step(
                    run_id, i, phase="cancelled", message="stopped"
                )
                for j in range(i + 1, len(cpus)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return

            benchmark_store.update_run(run_id, current_index=i)
            benchmark_store.update_step(run_id, i, phase="applying", message="")
            benchmark_log.write(
                f"run {run_id} step {i + 1}/{len(cpus)} {cpu} applying",
                source="sweep",
            )

            out = operators.set_resources(
                oid,
                req.nf,
                OperatorResourceSetRequest(
                    cpu_limit=cpu,
                    cpu_request=cpu,
                ),
            )
            gen = 0
            for n in out.nfs:
                if n.name == req.nf and n.desired is not None:
                    gen = int(n.desired.generation or 0)
                    break
            ok, err = wait_cpu_apply(oid, req.nf, gen, cancel=_cancel)
            if _cancel.is_set():
                benchmark_store.update_step(
                    run_id, i, phase="cancelled", message="stopped during apply"
                )
                for j in range(i + 1, len(cpus)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return
            if not ok:
                benchmark_log.write(
                    f"run {run_id} step {i + 1}/{len(cpus)} {cpu} error: {err}",
                    source="sweep",
                )
                benchmark_store.update_step(run_id, i, phase="error", message=err)
                for j in range(i + 1, len(cpus)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="aborted"
                    )
                benchmark_store.update_run(
                    run_id, status="error", message=err, finished=True
                )
                return

            benchmark_store.update_step(run_id, i, phase="warmup")
            benchmark_log.write(
                f"run {run_id} step {i + 1}/{len(cpus)} {cpu} warmup {req.warmup_sec}s",
                source="sweep",
            )
            if not _interruptible_sleep(req.warmup_sec, _cancel):
                benchmark_store.update_step(
                    run_id, i, phase="cancelled", message="stopped during warmup"
                )
                for j in range(i + 1, len(cpus)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return

            benchmark_store.update_step(run_id, i, phase="measuring", set_started=True)
            benchmark_log.write(
                f"run {run_id} step {i + 1}/{len(cpus)} {cpu} measure {req.step_sec}s",
                source="sweep",
            )
            if not _interruptible_sleep(req.step_sec, _cancel):
                tput = sample_throughput_mbps()
                benchmark_store.update_step(
                    run_id,
                    i,
                    phase="cancelled",
                    set_stopped=True,
                    message="stopped during measure",
                    throughput_mbps=tput,
                )
                for j in range(i + 1, len(cpus)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return

            tput = sample_throughput_mbps()
            benchmark_store.update_step(
                run_id,
                i,
                phase="done",
                set_stopped=True,
                throughput_mbps=tput,
            )
            tput_s = f" tput={tput}" if tput is not None else ""
            benchmark_log.write(
                f"run {run_id} step {i + 1}/{len(cpus)} {cpu} done{tput_s}",
                source="sweep",
            )

        benchmark_store.update_run(
            run_id, status="done", message="complete", finished=True
        )
        benchmark_log.write(f"run {run_id} complete", source="sweep")
    except Exception as exc:  # noqa: BLE001
        benchmark_log.write(f"run {run_id} exception: {exc}", source="sweep")
        benchmark_store.update_run(
            run_id, status="error", message=str(exc), finished=True
        )
    finally:
        global _thread, _active_id
        with _lock:
            if _active_id == run_id:
                _thread = None
                _active_id = None


def start_run(req: BenchmarkRunRequest) -> BenchmarkRunStatusOut:
    global _thread, _active_id
    cpus = cpu_steps(req.min_cpu, req.max_cpu, req.cpu_step)
    try:
        oid = _resolve_operator(req.operator_id, req.nf)
    except Exception as exc:
        benchmark_log.write(f"start failed: {exc}", source="sweep")
        raise
    with _lock:
        if _thread is not None and _thread.is_alive():
            raise RuntimeError("benchmark run already in progress")
        _cancel.clear()
        run_id = benchmark_store.create_run(
            operator_id=oid,
            nf=req.nf,
            min_cpu=req.min_cpu,
            max_cpu=req.max_cpu,
            steps=len(cpus),
            step_sec=req.step_sec,
            warmup_sec=req.warmup_sec,
            cpus=cpus,
        )
        t = threading.Thread(
            target=_worker,
            args=(run_id, req, cpus, oid),
            name=f"benchmark-run-{run_id}",
            daemon=True,
        )
        _thread = t
        _active_id = run_id
        t.start()
    benchmark_log.write(
        f"run {run_id} start operator={oid} nf={req.nf} "
        f"cpus={','.join(cpus)} step={req.step_sec}s warmup={req.warmup_sec}s "
        f"log={benchmark_log.log_path()}",
        source="sweep",
    )
    st = benchmark_store.get_run(run_id)
    assert st is not None
    return st


def stop_run() -> BenchmarkRunStopResponse:
    with _lock:
        running = _thread is not None and _thread.is_alive()
        if not running:
            st = benchmark_store.latest_run()
            benchmark_log.write("stop: no active run", source="sweep")
            return BenchmarkRunStopResponse(
                ok=True,
                message="no active benchmark run",
                status=st,
            )
        _cancel.set()
        benchmark_log.write("stop requested", source="sweep")
    st = status()
    return BenchmarkRunStopResponse(ok=True, message="stop requested", status=st)


def status() -> BenchmarkRunStatusOut:
    st = benchmark_store.latest_run()
    if st is not None and st.operator_id == "nws-xapp":
        st = benchmark_store.latest_run_excluding_operator("nws-xapp")
    if st is None:
        return BenchmarkRunStatusOut()
    with _lock:
        alive = _thread is not None and _thread.is_alive() and _active_id == st.id
    if alive:
        st.running = True
        st.status = "running"
    return st
