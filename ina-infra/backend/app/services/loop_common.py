"""Shared helpers: load PL snapshot and build ina.Slice objects for PM/PS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from app.schemas import ProfileRecord, ResourcesOut, SliceResultOut
from app.services import pl_solver, profile_store
from app.services.ina_path import ensure_ina_on_path


DeployMap = Dict[int, Tuple[int, int, int]]


@dataclass
class PlContext:
    """Runtime slice objects + placement from the last successful PL solve."""

    profile_name: str
    record: ProfileRecord
    slices: list  # list[ina.Slice]
    deploy_map: DeployMap
    network: object  # ina.Network


def _resources_from_out(r: ResourcesOut):
    ensure_ina_on_path()
    from ina.models import SliceResources

    return SliceResources(
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


def _slice_from_result(sr: SliceResultOut):
    ensure_ina_on_path()
    from ina import Slice

    s = Slice(
        id=sr.id,
        t_bar=sr.t_bar,
        d_bar=sr.d_bar,
        h_s=sr.h_s,
        eta_t0=sr.eta_t0,
        slice_type=sr.slice_type,
    )
    cu, upf, app = sr.placement.cu_id, sr.placement.upf_id, sr.placement.app_id
    s.placement = (cu, upf, app)
    s.resources = _resources_from_out(sr.resources)
    return s


def resources_to_out(res) -> ResourcesOut:
    return ResourcesOut(
        a_c_cu=res.a_c_cu,
        a_r_cu=res.a_r_cu,
        a_c_upf=res.a_c_upf,
        a_r_upf=res.a_r_upf,
        a_c_app=res.a_c_app,
        a_r_app=res.a_r_app,
        a_g_app=res.a_g_app,
        b_min=res.b_min,
        b_ded=res.b_ded,
    )


def load_pl_context(profile_name: str) -> PlContext:
    """Load profile and require a successful PL result."""
    record = profile_store.get_profile(profile_name)
    if record is None:
        raise ValueError(f"Profile {profile_name!r} not found")
    pl = record.pl_result
    if pl is None or not pl.ok or not pl.slices:
        raise ValueError(
            f"Profile {profile_name!r} has no successful PL result — solve PL first"
        )

    slices = [_slice_from_result(sr) for sr in pl.slices]
    deploy_map: DeployMap = {
        s.id: s.placement for s in slices if s.placement is not None
    }
    net = pl_solver._build_network(record.network)
    return PlContext(
        profile_name=profile_name,
        record=record,
        slices=slices,
        deploy_map=deploy_map,
        network=net,
    )
