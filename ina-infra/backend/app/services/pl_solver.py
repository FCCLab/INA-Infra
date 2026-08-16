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
    Profile,
    ProfileDefaultsOut,
    ResourcesOut,
    SliceIn,
    SliceResultOut,
)
from app.services import ip_allocator
from app.services.ina_path import ensure_ina_on_path

# Default 4-slice SLAs for profile ina-infra (see ina-infra/sla.md).
_DEFAULT_FOUR = [
    SliceIn(id=1, t_bar=10, d_bar=150, h_s=0, eta_t0=2.0, slice_type="CCTV"),
    SliceIn(id=2, t_bar=20, d_bar=20, h_s=1, eta_t0=2.0, slice_type="Physical AI"),
    SliceIn(id=3, t_bar=40, d_bar=50, h_s=0, eta_t0=2.5, slice_type="OTT"),
    SliceIn(id=4, t_bar=5, d_bar=150, h_s=0, eta_t0=1.5, slice_type="IoT"),
]


def _build_network(overrides: Optional[NetworkIn] = None):
    ensure_ina_on_path()
    from ina import Network

    net = Network()
    if overrides is None:
        return net
    data = overrides.model_dump(exclude_none=True)
    # Ignore removed fields
    data.pop("du_site", None)
    matrix_keys = {"d_n3", "d_n6"}
    for key, val in data.items():
        if not hasattr(net, key):
            continue
        if key == "d_f1" and isinstance(val, dict):
            # Accept CU-only {"0": ms} or pair "0-j" / "i-j" (use Edge-DU row i=0)
            site_map: dict[int, float] = {}
            for k, v in val.items():
                ks = str(k)
                if "-" in ks or "," in ks:
                    parts = ks.replace(",", "-").split("-")
                    if len(parts) == 2:
                        i, j = int(parts[0]), int(parts[1])
                        if i == 0:
                            site_map[j] = float(v)
                else:
                    site_map[int(ks)] = float(v)
            if site_map:
                setattr(net, key, site_map)
        elif key in matrix_keys and isinstance(val, dict):
            matrix = {}
            for k, v in val.items():
                parts = str(k).replace(",", "-").split("-")
                if len(parts) == 2:
                    matrix[(int(parts[0]), int(parts[1]))] = float(v)
            setattr(net, key, matrix)
        elif isinstance(val, dict):
            coerced = {}
            for k, v in val.items():
                try:
                    coerced[int(k)] = v
                except (TypeError, ValueError):
                    coerced[k] = v
            setattr(net, key, coerced)
        else:
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


def default_network_in() -> NetworkIn:
    """Snapshot of algorithm Network defaults as NetworkIn (for profile storage)."""
    ensure_ina_on_path()
    from ina import Network

    net = Network()

    def _pair_map(matrix: dict) -> dict[str, float]:
        return {f"{i}-{j}": float(v) for (i, j), v in matrix.items()}

    return NetworkIn(
        b_total=net.b_total,
        w_c=net.w_c,
        w_p=net.w_p,
        beta_demand=net.beta_demand,
        p_prb_ded=net.p_prb_ded,
        p_prb_prio=net.p_prb_prio,
        alpha_cu=net.alpha_cu,
        alpha_upf=net.alpha_upf,
        gamma_c=net.gamma_c,
        gamma_r=net.gamma_r,
        gamma_g=net.gamma_g,
        min_r_cu=net.min_r_cu,
        min_r_upf=net.min_r_upf,
        c_n_capacity=dict(net.c_n_capacity),
        r_n_capacity=dict(net.r_n_capacity),
        c_a_capacity=dict(net.c_a_capacity),
        r_a_capacity=dict(net.r_a_capacity),
        g_a_capacity=dict(net.g_a_capacity),
        p_c=dict(net.p_c),
        p_r=dict(net.p_r),
        p_g=dict(net.p_g),
        d_rf=net.d_rf,
        d_f1={str(k): float(v) for k, v in net.d_f1.items()},
        d_n3=_pair_map(net.d_n3),
        d_n6=_pair_map(net.d_n6),
    )


def default_slices() -> list[SliceIn]:
    """Return profile default 4-slice SLAs (CCTV / Physical AI / OTT / IoT)."""
    return list(_DEFAULT_FOUR)


def profile_defaults() -> ProfileDefaultsOut:
    from app.services import profile_store

    slices = list(_DEFAULT_FOUR)
    return ProfileDefaultsOut(
        profile=ip_allocator.default_profile(),
        slices=slices,
        network=default_network_in(),
        applications=profile_store.default_applications(slices),
    )


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

    from app.services import pl_result_store, profile_store

    profile = req.profile or Profile()
    if not req.slices:
        return PlSolveResponse(
            ok=False,
            message="No slices provided",
            profile=profile,
        )
    if len(req.slices) > profile.max_slices:
        return PlSolveResponse(
            ok=False,
            message=(
                f"N={len(req.slices)} exceeds profile.max_slices={profile.max_slices}"
            ),
            profile=profile,
        )

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
            profile=profile,
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

    try:
        ip_plan = ip_allocator.allocate_profile_ips(profile, req.slices, deploy_map)
    except ValueError as exc:
        return PlSolveResponse(
            ok=False,
            message=f"Placement ok but IP allocation failed: {exc}",
            deploy_map=deploy_map,
            resources=resources,
            slices=out_slices,
            profile=profile,
        )

    resp = PlSolveResponse(
        ok=True,
        message=f"Optimal placement + IP plan for {len(out_slices)} slice(s)",
        deploy_map=deploy_map,
        resources=resources,
        slices=out_slices,
        ip_plan=ip_plan,
        profile=profile,
    )

    # Persist input/output JSON under backend/results/ and attach to profile.
    try:
        path = pl_result_store.write_pl_run(req, resp, profile_name=profile.name)
        resp.result_file = str(path)
        saved = profile_store.save_pl_result(
            profile.name,
            resp,
            result_file=str(path),
            slices=list(req.slices),
            network=req.network,
        )
        if saved is not None:
            resp.message = (
                f"{resp.message}; saved to profile “{profile.name}” "
                f"and {path.name}"
            )
        else:
            resp.message = (
                f"{resp.message}; wrote {path.name} "
                f"(profile “{profile.name}” not in DB — Save profile first)"
            )
    except Exception as exc:  # noqa: BLE001
        resp.message = f"{resp.message}; warn: could not persist result ({exc})"

    return resp
