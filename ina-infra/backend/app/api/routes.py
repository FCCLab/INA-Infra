"""REST routes for INA-Infra."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import (
    NetworkIn,
    NetworkOut,
    PlApplyRequest,
    PlApplyResponse,
    PlSolveRequest,
    PlSolveResponse,
    ProfileCreateRequest,
    ProfileDefaultsOut,
    ProfileListOut,
    ProfileRecord,
    SliceIn,
)
from app.services import gitea_apply, pl_solver, profile_store

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ina-infra",
        "db": str(profile_store.db_path()),
    }


@router.get("/network", response_model=NetworkOut)
def get_network():
    return pl_solver.network_to_out()


@router.put("/network", response_model=NetworkOut)
def put_network(body: NetworkIn):
    """Return network with overrides applied (stateless preview)."""
    net = pl_solver._build_network(body)
    return pl_solver.network_to_out(net)


@router.get("/slices/defaults", response_model=list[SliceIn])
def get_default_slices():
    return pl_solver.default_slices()


@router.get("/profiles/default", response_model=ProfileDefaultsOut)
def get_profile_defaults():
    """Builtin defaults (also used to seed empty DB). Prefer GET /profiles."""
    return pl_solver.profile_defaults()


@router.get("/profiles", response_model=ProfileListOut)
def list_profiles():
    records = profile_store.list_profiles()
    return ProfileListOut(
        profiles=records,
        names=[r.profile.name for r in records],
    )


@router.get("/profiles/{name}", response_model=ProfileRecord)
def get_profile(name: str):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    return rec


@router.post("/profiles", response_model=ProfileRecord, status_code=201)
def create_profile(body: ProfileCreateRequest):
    slices = list(body.slices)
    network = body.network
    if body.copy_from:
        src = profile_store.get_profile(body.copy_from)
        if src is None:
            raise HTTPException(
                status_code=404,
                detail=f"copy_from profile not found: {body.copy_from}",
            )
        if not slices:
            slices = list(src.slices)
        if network is None:
            network = src.network
    if not slices:
        slices = pl_solver.default_slices()
    if network is None:
        network = pl_solver.default_network_in()
    try:
        return profile_store.create_profile(
            ProfileRecord(profile=body.profile, slices=slices, network=network)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/profiles/{name}", response_model=ProfileRecord)
def save_profile(name: str, body: ProfileRecord):
    """Create or update profile (identity + slices + network)."""
    if body.profile.name != name:
        raise HTTPException(
            status_code=400,
            detail=f"path name {name!r} != body.profile.name {body.profile.name!r}",
        )
    if not body.slices:
        raise HTTPException(status_code=400, detail="slices must not be empty")
    if body.network is None:
        body = body.model_copy(update={"network": pl_solver.default_network_in()})
    try:
        return profile_store.save_profile(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/profiles/{name}")
def delete_profile(name: str):
    deleted = profile_store.delete_profile(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    remaining = profile_store.list_profiles()
    return {
        "ok": True,
        "deleted": name,
        "remaining": [r.profile.name for r in remaining],
    }


@router.post("/pl/solve", response_model=PlSolveResponse)
def pl_solve(body: PlSolveRequest):
    try:
        return pl_solver.solve_pl(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/pl/apply", response_model=PlApplyResponse)
def pl_apply(body: PlApplyRequest):
    try:
        return gitea_apply.apply_to_gitea(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/pm/solve", status_code=501)
def pm_solve():
    raise HTTPException(
        status_code=501,
        detail="MediumLayer (PM) not implemented in this UI phase",
    )


@router.post("/ps/solve", status_code=501)
def ps_solve():
    raise HTTPException(
        status_code=501,
        detail="ShortLayer (PS) not implemented in this UI phase",
    )
