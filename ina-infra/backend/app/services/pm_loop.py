"""Medium-layer (PM) solve and background loop."""

from __future__ import annotations

import copy
import time
from typing import Iterator, Optional

from app.schemas import (
    PmLoopParams,
    PmLoopRequest,
    PmLoopStatusOut,
    PmLoopStopResponse,
    PmSliceResultOut,
    PmSolveResponse,
)
from app.services.cmd_stream import log_event, result_event, status_event
from app.services.ina_path import ensure_ina_on_path
from app.services.loop_application import get_adapter
from app.services.loop_common import load_pl_context, resources_to_out
from app.services.loop_state import (
    get_state,
    is_pm_running,
    register_pm,
    stop_pm,
    unregister_pm,
)


def _format_pm_log(resp: PmSolveResponse) -> str:
    lines = [f"PM cycle {resp.cycle}: ok={resp.ok} — {resp.message}"]
    for s in resp.slices:
        lines.append(
            f"  S{s.id}: demand={s.demand:.2f} compute_cap={s.compute_cap:.2f} "
            f"CU_cpu={s.resources.a_c_cu:.2f} UPF_cpu={s.resources.a_c_upf:.2f} "
            f"APP_cpu={s.resources.a_c_app:.2f}"
        )
    return "\n".join(lines)


def solve_pm_once(
    profile: str,
    params: Optional[PmLoopParams] = None,
    *,
    cycle: int = 0,
) -> PmSolveResponse:
    ensure_ina_on_path()
    from ina import MediumLayer

    params = params or PmLoopParams()
    ctx = load_pl_context(profile)
    state = get_state(profile)
    slices = copy.deepcopy(ctx.slices)

    for s in slices:
        base = state.demand.get(s.id, s.t_bar)
        s.demand = base * params.demand_multiplier

    updated = MediumLayer(ctx.network).solve(slices, ctx.deploy_map)
    if not updated:
        return PmSolveResponse(
            ok=False,
            profile=profile,
            cycle=cycle,
            message="MediumLayer failed (Gurobi did not return OPTIMAL)",
            demand=dict(state.demand),
        )

    out_slices: list[PmSliceResultOut] = []
    for s in slices:
        res = updated[s.id]
        cap = res.compute_cap(ctx.network)
        out_slices.append(
            PmSliceResultOut(
                id=s.id,
                demand=s.demand,
                compute_cap=cap,
                resources=resources_to_out(res),
            )
        )

    resp = PmSolveResponse(
        ok=True,
        profile=profile,
        cycle=cycle,
        message=f"PM optimal for {len(out_slices)} slice(s)",
        slices=out_slices,
        demand={s.id: s.demand for s in slices},
    )
    state.last_pm = resp
    state.pm_params = params
    state.pm_cycle = cycle
    get_adapter().on_pm(profile, cycle, updated, resp.demand)
    return resp


def pm_loop_status(profile: str) -> PmLoopStatusOut:
    st = get_state(profile)
    return PmLoopStatusOut(
        profile=profile,
        running=st.pm_running,
        cycle=st.pm_cycle,
        params=st.pm_params or PmLoopParams(),
        last_result=st.last_pm,
        demand=dict(st.demand),
    )


def stop_pm_loop(profile: str) -> PmLoopStopResponse:
    stopped = stop_pm(profile)
    return PmLoopStopResponse(
        ok=True,
        profile=profile,
        stopped=stopped,
        message=(
            f"Stop signalled for PM loop on {profile}"
            if stopped
            else f"No active PM loop for {profile}"
        ),
    )


def _sleep_interval(cancel, interval_sec: float) -> bool:
    """Sleep in 100ms chunks; return True if cancelled."""
    steps = max(1, int(interval_sec * 10))
    for _ in range(steps):
        if cancel.is_set():
            return True
        time.sleep(0.1)
    return cancel.is_set()


def iter_pm_loop_sse(req: PmLoopRequest) -> Iterator[str]:
    profile = req.profile.name
    if is_pm_running(profile):
        yield status_event(f"PM loop already running for {profile}")
        yield result_event(pm_loop_status(profile))
        return

    cancel = register_pm(profile)
    params = req.params or PmLoopParams()
    cycle = 0
    try:
        yield status_event(f"PM loop starting for {profile}", profile=profile)
        while not cancel.is_set():
            cycle += 1
            if params.max_cycles > 0 and cycle > params.max_cycles:
                yield log_event("stdout", f"PM reached max_cycles={params.max_cycles}")
                break
            resp = solve_pm_once(profile, params, cycle=cycle)
            yield log_event("stdout", _format_pm_log(resp))
            yield status_event(
                f"PM cycle {cycle} complete",
                cycle=cycle,
                ok=resp.ok,
            )
            if not resp.ok:
                break
            if _sleep_interval(cancel, params.interval_sec):
                yield log_event("stdout", "PM loop stopped")
                break
        yield result_event(pm_loop_status(profile))
    finally:
        unregister_pm(profile, cancel)
