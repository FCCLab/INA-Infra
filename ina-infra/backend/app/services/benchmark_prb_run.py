"""PRB-sweep runner: apply NS ratios via near-RT RIC xApp → warmup → measure.

Each step PATCHes dedicated/min/max on the xApp (E2 Slice SM). Throughput
sampling hooks match the CU-UP sweep (SQLite; UI shows start/stop).
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

from app.schemas import (
    BenchmarkPrbRunRequest,
    BenchmarkPrbRunStatusOut,
    BenchmarkPrbStopResponse,
    UeDesiredRequest,
)
from app.services import benchmark_log, benchmark_store, ues, xapp_prb

_lock = threading.Lock()
_cancel = threading.Event()
_thread: Optional[threading.Thread] = None
_active_id: Optional[int] = None

APPLY_TIMEOUT_SEC = 60.0
APPLY_POLL_SEC = 0.5


def prb_steps(min_prb: float, max_prb: float, prb_step: float) -> List[float]:
    if max_prb < min_prb:
        raise ValueError(f"max_prb {max_prb} < min_prb {min_prb}")
    if prb_step <= 0:
        raise ValueError("prb_step must be > 0")
    out: List[float] = []
    x = float(min_prb)
    # Avoid float drift; round to 0.01.
    while x <= max_prb + 1e-9:
        out.append(round(x, 2))
        x = round(x + prb_step, 2)
    if not out or abs(out[-1] - max_prb) > 1e-6:
        out.append(round(float(max_prb), 2))
    return out


def _fmt(level: float) -> str:
    return f"{level:g}%"


def normalize_direction(raw: str) -> str:
    d = (raw or "dl").strip().lower()
    if d not in ("dl", "ul"):
        raise ValueError(f"direction must be dl or ul, got: {raw!r}")
    return d


def ensure_traffic_direction(direction: str) -> str:
    """Align connected UE iperf reverse with PRB direction (dl→-R, ul→no -R)."""
    direction = normalize_direction(direction)
    reverse = direction == "dl"
    listed = ues.list_ues()
    online = [u for u in listed.ues if u.online]
    if not online:
        return "no UE online — leave traffic as-is"
    notes: List[str] = []
    for u in online:
        d = ues.set_desired(
            UeDesiredRequest(id=u.id, action="start", reverse=reverse)
        )
        notes.append(
            f"{u.id} {direction} reverse={d.reverse} gen={d.generation}"
        )
    return "; ".join(notes)


def _interruptible_sleep(seconds: float, cancel: threading.Event) -> bool:
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


def _ratios_close(
    *,
    dedicated: float,
    min_prb: float,
    max_prb: float,
    row: dict,
) -> bool:
    d = float(row.get("dedicated") or 0)
    mn = float(row.get("min") or 0)
    mx = float(row.get("max") or 0)
    return (
        abs(d - dedicated) <= 0.51
        and abs(mn - min_prb) <= 0.51
        and abs(mx - max_prb) <= 0.51
    )


def wait_prb_apply(
    *,
    sst: int,
    sd: str,
    direction: str,
    dedicated: float,
    min_prb: float,
    max_prb: float,
    timeout: float = APPLY_TIMEOUT_SEC,
    cancel: Optional[threading.Event] = None,
) -> Tuple[bool, str]:
    """PATCH via xApp; confirm via PATCH echo and/or GET indication.

    Slice SM indications can stall while E2 CONTROL still applies on the DU.
    Prefer the PATCH ``patched`` echo (CONTROL ACK path); soft-poll GET afterward.
    """
    ev = cancel or threading.Event()
    try:
        resp = xapp_prb.set_prb(
            sst=sst,
            sd=sd,
            direction=direction,
            dedicated=dedicated,
            min_prb=min_prb,
            max_prb=max_prb,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    patched = resp.get("patched") if isinstance(resp, dict) else None
    if isinstance(patched, dict) and _ratios_close(
        dedicated=dedicated, min_prb=min_prb, max_prb=max_prb, row=patched
    ):
        # Soft-confirm via GET for a few seconds (UI freshness); do not fail
        # if indications are stale — DU may already have applied.
        soft_deadline = time.monotonic() + min(2.0, timeout)
        want_sd = xapp_prb.normalize_sd(sd)
        while time.monotonic() < soft_deadline:
            if ev.is_set():
                return False, "cancelled"
            try:
                payload = xapp_prb.get_slices()
                row = xapp_prb.find_slice(
                    payload, sst=sst, sd=want_sd, direction=direction
                )
                if row is not None and _ratios_close(
                    dedicated=dedicated,
                    min_prb=min_prb,
                    max_prb=max_prb,
                    row=row,
                ):
                    return True, ""
            except Exception:  # noqa: BLE001
                pass
            ev.wait(APPLY_POLL_SEC)
        return True, "acked (indication stale)"

    deadline = time.monotonic() + timeout
    last = "waiting for indication"
    want_sd = xapp_prb.normalize_sd(sd)
    while time.monotonic() < deadline:
        if ev.is_set():
            return False, "cancelled"
        try:
            payload = xapp_prb.get_slices()
            row = xapp_prb.find_slice(
                payload, sst=sst, sd=want_sd, direction=direction
            )
            if row is None:
                last = f"slice sst={sst} sd={want_sd} {direction} missing"
            elif _ratios_close(
                dedicated=dedicated, min_prb=min_prb, max_prb=max_prb, row=row
            ):
                return True, ""
            else:
                d = float(row.get("dedicated") or 0)
                mn = float(row.get("min") or 0)
                mx = float(row.get("max") or 0)
                last = f"seen ded/min/max={d:g}/{mn:g}/{mx:g}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        ev.wait(APPLY_POLL_SEC)
    return False, f"apply timeout ({last})"


def sample_throughput_mbps() -> Optional[float]:
    return None


def _worker(run_id: int, req: BenchmarkPrbRunRequest, levels: List[float]) -> None:
    try:
        try:
            traffic_note = ensure_traffic_direction(req.direction)
            benchmark_log.write(
                f"prb run {run_id} traffic: {traffic_note}", source="prb"
            )
        except Exception as exc:  # noqa: BLE001
            benchmark_log.write(
                f"prb run {run_id} traffic direction failed: {exc}", source="prb"
            )
            benchmark_store.update_run(
                run_id,
                status="error",
                message=f"traffic direction: {exc}",
                finished=True,
            )
            return

        for i, level in enumerate(levels):
            if _cancel.is_set():
                benchmark_store.update_step(
                    run_id, i, phase="cancelled", message="stopped"
                )
                for j in range(i + 1, len(levels)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return

            # Sweep max; keep dedicated/min fixed (must stay ≤ level).
            dedicated = float(req.dedicated)
            min_prb = float(req.min_prb)
            max_prb = float(level)
            if dedicated > min_prb:
                raise ValueError("dedicated > min_prb")
            if min_prb > max_prb:
                # Clamp min/dedicated down so the step is still valid.
                min_prb = max_prb
                dedicated = min(dedicated, min_prb)

            label = (
                f"d/m/M={dedicated:g}/{min_prb:g}/{max_prb:g}"
            )
            benchmark_store.update_run(run_id, current_index=i)
            benchmark_store.update_step(
                run_id, i, phase="applying", message="", cpu=label
            )

            benchmark_log.write(
                f"prb run {run_id} step {i + 1}/{len(levels)} {label} applying "
                f"sst={req.sst} sd={req.sd} dir={req.direction}",
                source="prb",
            )
            ok, err = wait_prb_apply(
                sst=req.sst,
                sd=req.sd,
                direction=req.direction,
                dedicated=dedicated,
                min_prb=min_prb,
                max_prb=max_prb,
                cancel=_cancel,
            )
            if err and ok:
                benchmark_store.update_step(run_id, i, message=err)
                benchmark_log.write(
                    f"prb run {run_id} step {i + 1} apply note: {err}",
                    source="prb",
                )
            if _cancel.is_set():
                benchmark_store.update_step(
                    run_id, i, phase="cancelled", message="stopped during apply"
                )
                for j in range(i + 1, len(levels)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return
            if not ok:
                benchmark_log.write(
                    f"prb run {run_id} step {i + 1} error: {err}", source="prb"
                )
                benchmark_store.update_step(run_id, i, phase="error", message=err)
                for j in range(i + 1, len(levels)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="aborted"
                    )
                benchmark_store.update_run(
                    run_id, status="error", message=err, finished=True
                )
                return

            benchmark_store.update_step(run_id, i, phase="warmup")
            if not _interruptible_sleep(req.warmup_sec, _cancel):
                benchmark_store.update_step(
                    run_id, i, phase="cancelled", message="stopped during warmup"
                )
                for j in range(i + 1, len(levels)):
                    benchmark_store.update_step(
                        run_id, j, phase="skipped", message="stopped"
                    )
                benchmark_store.update_run(
                    run_id, status="stopped", message="stopped", finished=True
                )
                return

            benchmark_store.update_step(run_id, i, phase="measuring", set_started=True)
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
                for j in range(i + 1, len(levels)):
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
            benchmark_log.write(
                f"prb run {run_id} step {i + 1}/{len(levels)} {label} done",
                source="prb",
            )

        benchmark_store.update_run(
            run_id, status="done", message="complete", finished=True
        )
        benchmark_log.write(f"prb run {run_id} complete", source="prb")
    except Exception as exc:  # noqa: BLE001
        benchmark_log.write(f"prb run {run_id} exception: {exc}", source="prb")
        benchmark_store.update_run(
            run_id, status="error", message=str(exc), finished=True
        )
    finally:
        global _thread, _active_id
        with _lock:
            if _active_id == run_id:
                _thread = None
                _active_id = None


def start_run(req: BenchmarkPrbRunRequest) -> BenchmarkPrbRunStatusOut:
    global _thread, _active_id
    direction = normalize_direction(req.direction)
    req = req.model_copy(update={"direction": direction})
    levels = prb_steps(req.sweep_min, req.sweep_max, req.prb_step)
    if req.dedicated > req.min_prb:
        raise ValueError("dedicated must be ≤ min_prb")
    if req.min_prb > req.sweep_min:
        raise ValueError(
            f"min_prb {req.min_prb} > sweep_min {req.sweep_min} "
            "(min must stay ≤ every max step)"
        )
    # Probe xApp reachability before creating the run.
    try:
        xapp_prb.get_slices()
    except Exception as exc:  # noqa: BLE001
        benchmark_log.write(f"prb start failed: {exc}", source="prb")
        raise ValueError(str(exc)) from exc

    labels = [_fmt(x) for x in levels]
    with _lock:
        if _thread is not None and _thread.is_alive():
            raise RuntimeError("PRB benchmark run already in progress")
        _cancel.clear()
        run_id = benchmark_store.create_run(
            operator_id="nws-xapp",
            nf=f"sst={req.sst}/sd={xapp_prb.normalize_sd(req.sd)}/{direction}",
            min_cpu=str(req.sweep_min),
            max_cpu=str(req.sweep_max),
            steps=len(levels),
            step_sec=req.step_sec,
            warmup_sec=req.warmup_sec,
            cpus=labels,
        )
        t = threading.Thread(
            target=_worker,
            args=(run_id, req, levels),
            name=f"benchmark-prb-{run_id}",
            daemon=True,
        )
        _thread = t
        _active_id = run_id
        t.start()
    benchmark_log.write(
        f"prb run {run_id} start levels={','.join(labels)} "
        f"ded/min={req.dedicated:g}/{req.min_prb:g} "
        f"sst={req.sst} sd={req.sd} dir={direction} "
        f"xapp={xapp_prb.xapp_base_url()}",
        source="prb",
    )
    return status_for(run_id)


def stop_run() -> BenchmarkPrbStopResponse:
    with _lock:
        running = _thread is not None and _thread.is_alive()
        if not running:
            st = status()
            benchmark_log.write("prb stop: no active run", source="prb")
            return BenchmarkPrbStopResponse(
                ok=True, message="no active PRB benchmark run", status=st
            )
        _cancel.set()
        benchmark_log.write("prb stop requested", source="prb")
    return BenchmarkPrbStopResponse(ok=True, message="stop requested", status=status())


def status_for(run_id: int) -> BenchmarkPrbRunStatusOut:
    st = benchmark_store.get_run(run_id)
    if st is None:
        return BenchmarkPrbRunStatusOut()
    with _lock:
        alive = _thread is not None and _thread.is_alive() and _active_id == st.id
    out = BenchmarkPrbRunStatusOut(
        id=st.id,
        running=alive,
        status="running" if alive else st.status,
        message=st.message,
        operator_id=st.operator_id,
        nf=st.nf,
        sweep_min=float(st.min_cpu or 0),
        sweep_max=float(st.max_cpu or 0),
        steps=st.steps,
        step_sec=st.step_sec,
        warmup_sec=st.warmup_sec,
        current_index=st.current_index,
        started_at=st.started_at,
        finished_at=st.finished_at,
        step_list=st.step_list,
    )
    return out


def status() -> BenchmarkPrbRunStatusOut:
    st = benchmark_store.latest_run_for_operator("nws-xapp")
    if st is None:
        with _lock:
            alive = _thread is not None and _thread.is_alive()
        return BenchmarkPrbRunStatusOut(running=alive)
    return status_for(int(st.id))  # type: ignore[arg-type]
