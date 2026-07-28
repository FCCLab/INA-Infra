"""REST routes for INA-Infra."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas import (
    NetworkIn,
    NetworkOut,
    PlApplyRequest,
    PlApplyResponse,
    PlSolveRequest,
    PlSolveResponse,
    PlUndeployRequest,
    PlUndeployResponse,
    ProfileClusterStatusOut,
    ProfileCreateRequest,
    ProfileDefaultsOut,
    ProfileListOut,
    ProfileRecord,
    ProfileRolloutRequest,
    ProfileRolloutResponse,
    ProfileRolloutStopResponse,
    SliceIn,
)
from app.services import (
    cluster_status,
    gitea_apply,
    pl_solver,
    profile_rollout,
    profile_store,
)

router = APIRouter(prefix="/api/v1")

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_response(gen):
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


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


@router.get("/profiles/{name}/cluster-status", response_model=ProfileClusterStatusOut)
def get_profile_cluster_status(name: str):
    """Deployment readiness for the profile namespace on central."""
    try:
        raw = cluster_status.profile_cluster_status(name)
        return ProfileClusterStatusOut.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/profiles/{name}/rollout",
    response_model=ProfileRolloutResponse,
)
def post_profile_rollout(name: str, body: ProfileRolloutRequest | None = None):
    """Staged pod restart: UPF → SMF → PFCP → CU-CP → CU-UP → DU → UEs."""
    try:
        return profile_rollout.run_profile_rollout(name, body or ProfileRolloutRequest())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/profiles/{name}/rollout/stream")
def post_profile_rollout_stream(
    name: str, body: ProfileRolloutRequest | None = None
):
    """SSE: live stdout/stderr from staged profile rollout."""
    return _sse_response(
        profile_rollout.iter_profile_rollout_sse(
            name, body or ProfileRolloutRequest()
        )
    )


@router.post(
    "/profiles/{name}/rollout/stop",
    response_model=ProfileRolloutStopResponse,
)
def post_profile_rollout_stop(name: str):
    """Stop an in-progress staged profile rollout (kills script + ssh children)."""
    return profile_rollout.stop_profile_rollout(name)


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


@router.post("/pl/apply/stream")
def pl_apply_stream(body: PlApplyRequest):
    """SSE: live Gitea push output for deploy / dry-deploy."""
    return _sse_response(gitea_apply.iter_apply_sse(body))


@router.post("/pl/undeploy", response_model=PlUndeployResponse)
def pl_undeploy(body: PlUndeployRequest):
    try:
        return gitea_apply.undeploy_from_gitea(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/pl/undeploy/stream")
def pl_undeploy_stream(body: PlUndeployRequest):
    """SSE: live Gitea push + cleanup output for undeploy."""
    return _sse_response(gitea_apply.iter_undeploy_sse(body))


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
