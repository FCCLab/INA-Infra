"""Short-layer (PS) solve and background loop."""

from __future__ import annotations

import copy
import random
from typing import Iterator, Optional

from app.schemas import (
    PsLoopParams,
    PsLoopRequest,
    PsLoopStatusOut,
    PsLoopStopResponse,
    PsSliceResultOut,
    PsSolveResponse,
)
from app.services.cmd_stream import log_event, result_event, status_event
from app.services.ina_path import ensure_ina_on_path
from app.services.loop_application import get_adapter
from app.services.loop_common import load_pl_context
from app.services.loop_state import (
    get_state,
    is_ps_running,
    register_ps,
    stop_ps,
    unregister_ps,
)


def _pick_mcs(params: PsLoopParams, rng: random.Random) -> int:
    if params.mcs_fixed is not None:
        return int(params.mcs_fixed)
    lo = min(params.mcs_min, params.mcs_max)
    hi = max(params.mcs_min, params.mcs_max)
    return rng.randint(lo, hi)


def _format_ps_log(resp: PsSolveResponse) -> str:
    lines = [f"PS cycle {resp.cycle}: ok={resp.ok} — {resp.message}"]
    for s in resp.slices:
        lines.append(
            f"  S{s.id}: eta={s.eta:.4f} b_min={s.b_min:.0f} b_ded={s.b_ded:.0f} "
            f"b_max={s.b_max:.1f} radio≈{s.radio_mbps:.2f} Mbps demand→PM={s.demand:.2f}"
        )
    lines.append(f"  shared extra={resp.extra:.1f} PRBs/slice")
    return "\n".join(lines)


def solve_ps_once(
    profile: str,
    params: Optional[PsLoopParams] = None,
    *,
    cycle: int = 0,
    rng: Optional[random.Random] = None,
) -> PsSolveResponse:
    ensure_ina_on_path()
    from ina import EtaCalculator, ShortLayer

    params = params or PsLoopParams()
    ctx = load_pl_context(profile)
    state = get_state(profile)
    slices = copy.deepcopy(ctx.slices)
    eta_calc = EtaCalculator()
    rng = rng or random.Random()

    for s in slices:
        mcs = _pick_mcs(params, rng)
        s.eta = eta_calc.calculate(mcs)

    ps = ShortLayer(ctx.network).solve(slices)
    if not ps.ok:
        return PsSolveResponse(
            ok=False,
            profile=profile,
            cycle=cycle,
            message="ShortLayer failed (Gurobi did not return OPTIMAL)",
            demand=dict(state.demand),
        )

    out_slices: list[PsSliceResultOut] = []
    for s in slices:
        b_min = ps.b_min[s.id]
        b_ded = ps.b_ded[s.id]
        b_max = ps.b_max[s.id]
        radio = b_max * s.eta
        state.demand[s.id] = radio
        out_slices.append(
            PsSliceResultOut(
                id=s.id,
                eta=s.eta,
                b_min=b_min,
                b_ded=b_ded,
                b_max=b_max,
                radio_mbps=radio,
                demand=radio,
            )
        )

    resp = PsSolveResponse(
        ok=True,
        profile=profile,
        cycle=cycle,
        message=f"PS optimal for {len(out_slices)} slice(s)",
        slices=out_slices,
        extra=ps.extra,
        demand=dict(state.demand),
    )
    state.last_ps = resp
    state.ps_params = params
    state.ps_cycle = cycle
    get_adapter().on_ps(profile, cycle, ps, resp.demand)
    return resp


def ps_loop_status(profile: str) -> PsLoopStatusOut:
    st = get_state(profile)
    return PsLoopStatusOut(
        profile=profile,
        running=st.ps_running,
        cycle=st.ps_cycle,
        params=st.ps_params or PsLoopParams(),
        last_result=st.last_ps,
        demand=dict(st.demand),
    )


def stop_ps_loop(profile: str) -> PsLoopStopResponse:
    stopped = stop_ps(profile)
    return PsLoopStopResponse(
        ok=True,
        profile=profile,
        stopped=stopped,
        message=(
            f"Stop signalled for PS loop on {profile}"
            if stopped
            else f"No active PS loop for {profile}"
        ),
    )


def _sleep_interval(cancel, interval_sec: float) -> bool:
    import time

    steps = max(1, int(interval_sec * 10))
    for _ in range(steps):
        if cancel.is_set():
            return True
        time.sleep(0.1)
    return cancel.is_set()


def iter_ps_loop_sse(req: PsLoopRequest) -> Iterator[str]:
    profile = req.profile.name
    if is_ps_running(profile):
        yield status_event(f"PS loop already running for {profile}")
        yield result_event(ps_loop_status(profile))
        return

    cancel = register_ps(profile)
    params = req.params or PsLoopParams()
    rng = random.Random(params.seed)
    cycle = 0
    try:
        yield status_event(f"PS loop starting for {profile}", profile=profile)
        while not cancel.is_set():
            cycle += 1
            if params.max_cycles > 0 and cycle > params.max_cycles:
                yield log_event("stdout", f"PS reached max_cycles={params.max_cycles}")
                break
            resp = solve_ps_once(profile, params, cycle=cycle, rng=rng)
            yield log_event("stdout", _format_ps_log(resp))
            yield status_event(
                f"PS cycle {cycle} complete",
                cycle=cycle,
                ok=resp.ok,
            )
            if not resp.ok:
                break
            if _sleep_interval(cancel, params.interval_sec):
                yield log_event("stdout", "PS loop stopped")
                break
        yield result_event(ps_loop_status(profile))
    finally:
        unregister_ps(profile, cancel)
