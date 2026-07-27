"""Wrap PlanningLayer for the REST API."""

from __future__ import annotations

from typing import Optional

from app.schemas import (
    LOC_NAMES,
    NetworkIn,
    NetworkOut,
    PlacementOut,
    PlSolveRequest,
    PlSolveResponse,
    ResourcesOut,
    SliceIn,
    SliceResultOut,
)
from app.services.ina_path import ensure_ina_on_path


def _build_network(overrides: Optional[NetworkIn] = None):
    ensure_ina_on_path()
    from ina import Network

    net = Network()
    if overrides is None:
        return net
    data = overrides.model_dump(exclude_none=True)
    for key, val in data.items():
        if hasattr(net, key):
            setattr(net, key, val)
    return net


def network_to_out(net=None) -> NetworkOut:
    ensure_ina_on_path()
    from ina import Network

    net = net or Network()
    return NetworkOut(
        settings_text=net.format_settings(),
        b_total=net.b_total,
        w_c=net.w_c,
        w_p=net.w_p,
        locations=[LOC_NAMES[j] for j in net.locations],
        c_n_capacity=dict(net.c_n_capacity),
        r_n_capacity=dict(net.r_n_capacity),
        c_a_capacity=dict(net.c_a_capacity),
        r_a_capacity=dict(net.r_a_capacity),
        g_a_capacity=dict(net.g_a_capacity),
    )


def default_slices() -> list[SliceIn]:
    ensure_ina_on_path()
    from ina import make_slices

    return [
        SliceIn(
            id=s.id,
            t_bar=s.t_bar,
            d_bar=s.d_bar,
            h_s=s.h_s,
            eta_t0=s.eta_t0,
            slice_type=s.slice_type,
        )
        for s in make_slices(seed=2025)
    ]


def _placement_out(cu: int, upf: int, app: int) -> PlacementOut:
    return PlacementOut(
        cu=LOC_NAMES[cu],
        upf=LOC_NAMES[upf],
        app=LOC_NAMES[app],
        cu_id=cu,
        upf_id=upf,
        app_id=app,
    )


def solve_pl(req: PlSolveRequest) -> PlSolveResponse:
    ensure_ina_on_path()
    from ina import PlanningLayer, Slice

    if not req.slices:
        return PlSolveResponse(ok=False, message="No slices provided")

    slices = [
        Slice(
            id=s.id,
            t_bar=s.t_bar,
            d_bar=s.d_bar,
            h_s=s.h_s,
            eta_t0=s.eta_t0,
            slice_type=s.slice_type,
        )
        for s in req.slices
    ]
    net = _build_network(req.network)
    result = PlanningLayer(net).solve(slices)
    if not result.ok:
        return PlSolveResponse(
            ok=False,
            message="PlanningLayer failed (Gurobi did not return OPTIMAL)",
        )

    deploy_map: dict[str, PlacementOut] = {}
    resources: dict[str, ResourcesOut] = {}
    out_slices: list[SliceResultOut] = []

    for s in slices:
        cu, upf, app = result.deploy_map[s.id]
        place = _placement_out(cu, upf, app)
        r = result.resources[s.id]
        res = ResourcesOut(
            a_c_cu=r.a_c_cu,
            a_r_cu=r.a_r_cu,
            a_c_upf=r.a_c_upf,
            a_r_upf=r.a_r_upf,
            a_c_app=r.a_c_app,
            a_r_app=r.a_r_app,
            a_g_app=r.a_g_app,
            b_min=r.b_min,
            b_ded=r.b_ded,
        )
        key = str(s.id)
        deploy_map[key] = place
        resources[key] = res
        out_slices.append(
            SliceResultOut(
                id=s.id,
                slice_type=s.slice_type,
                t_bar=s.t_bar,
                d_bar=s.d_bar,
                h_s=s.h_s,
                eta_t0=s.eta_t0,
                placement=place,
                resources=res,
            )
        )

    return PlSolveResponse(
        ok=True,
        message=f"Optimal placement for {len(out_slices)} slice(s)",
        deploy_map=deploy_map,
        resources=resources,
        slices=out_slices,
    )
