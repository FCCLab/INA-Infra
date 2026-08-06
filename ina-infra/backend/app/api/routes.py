"""REST routes for INA-Infra (grouped OpenAPI tags)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas import (
    EdgeNodesOut,
    NetworkIn,
    NetworkOut,
    PlApplyRequest,
    PlApplyResponse,
    PlPushRequest,
    PlPushResponse,
    PlSolveRequest,
    PlSolveResponse,
    PlUndeployRequest,
    PlUndeployResponse,
    PmLoopRequest,
    PmLoopStatusOut,
    PmLoopStopResponse,
    PmSolveResponse,
    ProfileClusterStatusOut,
    ProfileCreateRequest,
    ProfileDefaultsOut,
    ProfileListOut,
    ProfileRecord,
    ProfileRolloutRequest,
    ProfileRolloutResponse,
    ProfileRolloutStopResponse,
    PsLoopRequest,
    PsLoopStatusOut,
    PsLoopStopResponse,
    PsSolveResponse,
    SliceIn,
)
from app.services import (
    cluster_status,
    gitea_apply,
    pl_solver,
    pm_loop,
    profile_rollout,
    profile_store,
    ps_loop,
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


# ── System ───────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description=(
        "Liveness probe for the host backend process. "
        "Returns service name and absolute path to the SQLite profile DB "
        "(`INA_DB_PATH`)."
    ),
)
def health():
    return {
        "status": "ok",
        "service": "ina-infra",
        "db": str(profile_store.db_path()),
    }


# ── Network ──────────────────────────────────────────────────────────────────


@router.get(
    "/network",
    response_model=NetworkOut,
    tags=["Network"],
    summary="Get substrate network defaults",
    description=(
        "Return the PlanningLayer substrate model used for PL solve: "
        "site compute/radio capacities, PRB pool (`b_total`), and cost weights. "
        "Values come from the in-process Network defaults (not per-profile)."
    ),
)
def get_network():
    return pl_solver.network_to_out()


@router.put(
    "/network",
    response_model=NetworkOut,
    tags=["Network"],
    summary="Preview network overrides",
    description=(
        "Apply optional field overrides on top of substrate defaults and return "
        "the merged Network view. **Stateless** — does not persist; use profile "
        "`network` on Save / PL solve to keep overrides with a profile."
    ),
)
def put_network(body: NetworkIn):
    net = pl_solver._build_network(body)
    return pl_solver.network_to_out(net)


# ── Slices ───────────────────────────────────────────────────────────────────


@router.get(
    "/slices/defaults",
    response_model=list[SliceIn],
    tags=["Slices"],
    summary="Default slice SLA templates",
    description=(
        "Builtin demo slice list (CCTV / Physical AI / OTT / IoT) with "
        "`t_bar`, `d_bar`, `h_s`, `eta_t0`. Used when creating a profile "
        "without an explicit slice list."
    ),
)
def get_default_slices():
    return pl_solver.default_slices()


# ── Profiles ─────────────────────────────────────────────────────────────────


@router.get(
    "/profiles/default",
    response_model=ProfileDefaultsOut,
    tags=["Profiles"],
    summary="Builtin profile seed",
    description=(
        "Return the builtin profile identity + default slices + network used "
        "to seed an empty DB. Prefer `GET /profiles` for saved profiles."
    ),
)
def get_profile_defaults():
    return pl_solver.profile_defaults()


@router.get(
    "/profiles",
    response_model=ProfileListOut,
    tags=["Profiles"],
    summary="List saved profiles",
    description=(
        "List all profiles in SQLite, including last PL result metadata and "
        "deploy flags. `names` is a convenience list of profile name strings."
    ),
)
def list_profiles():
    records = [
        gitea_apply.attach_local_deploy_files(r)
        for r in profile_store.list_profiles()
    ]
    return ProfileListOut(
        profiles=records,
        names=[r.profile.name for r in records],
    )


@router.post(
    "/profiles",
    response_model=ProfileRecord,
    status_code=201,
    tags=["Profiles"],
    summary="Create profile",
    description=(
        "Create a new profile (K8s namespace name). Optionally `copy_from` "
        "another profile for slices/network. Per-profile identity "
        "(Multus `subnet`, `dnn_prefix`, max slices, RAN nodes) is kept: "
        "if Multus / DNN collide with an existing profile, the next free "
        "`10.1.14x.0/24` / `10.14x` is assigned. If slices/network omitted, "
        "builtins are used. Returns **409** if the name already exists."
    ),
)
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
    # Multus / DNN are per-profile (copy_from usually clones the same values).
    profile = profile_store.allocate_profile_identity_for_create(body.profile)
    try:
        return profile_store.create_profile(
            ProfileRecord(profile=profile, slices=slices, network=network)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/profiles/{name}",
    response_model=ProfileRecord,
    tags=["Profiles"],
    summary="Get profile",
    description=(
        "Load one saved profile: identity (`name`, Multus `subnet`, DNN prefix, "
        "DU/UE nodes), slice SLAs, network overrides, and last successful PL "
        "result / deploy state if any."
    ),
)
def get_profile(name: str):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    return gitea_apply.attach_local_deploy_files(rec)


@router.put(
    "/profiles/{name}",
    response_model=ProfileRecord,
    tags=["Profiles"],
    summary="Save / upsert profile",
    description=(
        "Create or update profile identity + slices + network. Path `{name}` "
        "must match `body.profile.name`. Slices must be non-empty. Does **not** "
        "push GitOps — use Planning → Apply for that."
    ),
)
def save_profile(name: str, body: ProfileRecord):
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


@router.post(
    "/profiles/{name}/restore-defaults",
    response_model=ProfileRecord,
    tags=["Profiles"],
    summary="Restore profile defaults",
    description=(
        "Reset profile identity fields, slices, and network to builtins while "
        "keeping the profile **name** and deploy-related state. Returns **404** "
        "if the profile does not exist."
    ),
)
def restore_profile_defaults(name: str):
    try:
        return profile_store.restore_profile_defaults(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/profiles/{name}",
    tags=["Profiles"],
    summary="Delete profile",
    description=(
        "Remove a profile from SQLite. If the DB would be empty, the builtin "
        "`ina-infra` profile is re-seeded. Does **not** undeploy cluster "
        "workloads — call Planning → Undeploy first if needed."
    ),
)
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


# ── Clusters ─────────────────────────────────────────────────────────────────


@router.get(
    "/clusters/edge/nodes",
    response_model=EdgeNodesOut,
    tags=["Clusters"],
    summary="List edge cluster nodes",
    description=(
        "Discover nodes on the **edge** cluster (kubectl) for DU / UE "
        "`kubernetes.io/hostname` placement. Includes Ready state, Multus "
        "parent label when present, and suggested `default_du` / `default_ue`."
    ),
)
def get_edge_nodes():
    try:
        return cluster_status.list_edge_nodes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/profiles/{name}/cluster-status",
    response_model=ProfileClusterStatusOut,
    tags=["Clusters"],
    summary="Profile workload status",
    description=(
        "Best-effort Deployment readiness for the profile namespace across "
        "central / regional / edge. Reads live cluster state and placement "
        "hints from `ina-pl-placement` when present."
    ),
)
def get_profile_cluster_status(name: str):
    try:
        raw = cluster_status.profile_cluster_status(name)
        return ProfileClusterStatusOut.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Rollout ──────────────────────────────────────────────────────────────────


@router.post(
    "/profiles/{name}/rollout",
    response_model=ProfileRolloutResponse,
    tags=["Rollout"],
    summary="Staged NF rollout (blocking)",
    description=(
        "Run the staged restart script for the profile namespace: "
        "NRF → UPF → SMF → PFCP wait → CU-CP → CU-UP → DU → UEs. "
        "Blocks until the script finishes. Prefer `/rollout/stream` for UI logs."
    ),
)
def post_profile_rollout(name: str, body: ProfileRolloutRequest | None = None):
    try:
        return profile_rollout.run_profile_rollout(name, body or ProfileRolloutRequest())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/profiles/{name}/rollout/stream",
    tags=["Rollout"],
    summary="Staged NF rollout (SSE)",
    description=(
        "Same staged rollout as `/rollout`, but streams stdout/stderr as "
        "**Server-Sent Events** (`text/event-stream`) for live UI progress."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from the rollout script",
        }
    },
)
def post_profile_rollout_stream(
    name: str, body: ProfileRolloutRequest | None = None
):
    return _sse_response(
        profile_rollout.iter_profile_rollout_sse(
            name, body or ProfileRolloutRequest()
        )
    )


@router.post(
    "/profiles/{name}/rollout/stop",
    response_model=ProfileRolloutStopResponse,
    tags=["Rollout"],
    summary="Stop in-progress rollout",
    description=(
        "Kill an in-progress staged rollout for this profile (script process "
        "and SSH children). Safe to call when no rollout is running."
    ),
)
def post_profile_rollout_stop(name: str):
    return profile_rollout.stop_profile_rollout(name)


# ── Planning (PL) ────────────────────────────────────────────────────────────


@router.post(
    "/pl/solve",
    response_model=PlSolveResponse,
    tags=["Planning (PL)"],
    summary="Solve placement + Multus IP plan",
    description=(
        "Run PlanningLayer (Gurobi): place CU / UPF / APP per slice from SLAs, "
        "allocate Multus IPs on the profile subnet "
        "(`host = base[role] + n`, shared Nnrf at `.11`). "
        "Persists the result on the profile and under `backend/results/`."
    ),
)
def pl_solve(body: PlSolveRequest):
    try:
        return pl_solver.solve_pl(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/pl/apply",
    response_model=PlApplyResponse,
    tags=["Planning (PL)"],
    summary="Apply profile to GitOps (blocking)",
    description=(
        "Purge+rewrite `repos/{central,regional,edge}-repo/namespaces/<profile>/` "
        "from templates (NADs, IP ConfigMaps, dedicated core, UPF/CU-UP/RAN), "
        "render OAI controllers with Multus Nnrf from the IP plan, then push "
        "to Gitea. Set `dry_run` to render without push. Blocks until push ends."
    ),
)
def pl_apply(body: PlApplyRequest):
    try:
        return gitea_apply.apply_to_gitea(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/pl/apply/stream",
    tags=["Planning (PL)"],
    summary="Apply profile to GitOps (SSE)",
    description=(
        "Same as `/pl/apply` but streams Gitea push output as "
        "**Server-Sent Events** for the Deploy UI."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from render + git push",
        }
    },
)
def pl_apply_stream(body: PlApplyRequest):
    return _sse_response(gitea_apply.iter_apply_sse(body))


@router.post(
    "/pl/push",
    response_model=PlPushResponse,
    tags=["Planning (PL)"],
    summary="Push rendered GitOps to Gitea (blocking)",
    description=(
        "Push already-rendered `namespaces/<profile>/` trees to Gitea without "
        "re-rendering. Pair with Generate config (= Deploy) or Clear "
        "(= Undeploy git sync)."
    ),
)
def pl_push(body: PlPushRequest):
    try:
        return gitea_apply.push_to_gitea(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/pl/push/stream",
    tags=["Planning (PL)"],
    summary="Push rendered GitOps to Gitea (SSE)",
    description="Same as `/pl/push` with live **SSE** logs.",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from git push",
        }
    },
)
def pl_push_stream(body: PlPushRequest):
    return _sse_response(gitea_apply.iter_push_sse(body))


@router.post(
    "/pl/undeploy",
    response_model=PlUndeployResponse,
    tags=["Planning (PL)"],
    summary="Clear or Undeploy profile GitOps (blocking)",
    description=(
        "`dry_run=true` (Clear): remove local `namespaces/<profile>/` and "
        "clear deploy state; skip push. `dry_run=false` (Undeploy): clear + "
        "push to Gitea + best-effort cluster namespace cleanup."
    ),
)
def pl_undeploy(body: PlUndeployRequest):
    try:
        return gitea_apply.undeploy_from_gitea(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/pl/undeploy/stream",
    tags=["Planning (PL)"],
    summary="Clear or Undeploy profile GitOps (SSE)",
    description=(
        "Same as `/pl/undeploy` with live **SSE** logs for push and cluster "
        "cleanup."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from undeploy",
        }
    },
)
def pl_undeploy_stream(body: PlUndeployRequest):
    return _sse_response(gitea_apply.iter_undeploy_sse(body))


# ── Medium (PM) ──────────────────────────────────────────────────────────────


@router.post(
    "/pm/solve",
    response_model=PmSolveResponse,
    tags=["Medium (PM)"],
    summary="Run PM once",
    description=(
        "Single MediumLayer solve using placement from the profile's last PL "
        "result. Reads demand from PS loop state (fallback: slice t_bar)."
    ),
)
def pm_solve(body: PmLoopRequest):
    try:
        return pm_loop.solve_pm_once(body.profile.name, body.params, cycle=0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/pm/loop/start",
    tags=["Medium (PM)"],
    summary="Start PM background loop (SSE)",
    description=(
        "Run MediumLayer on an interval until Stop or max_cycles. Streams "
        "cycle logs as Server-Sent Events."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from PM loop",
        }
    },
)
def pm_loop_start(body: PmLoopRequest):
    return _sse_response(pm_loop.iter_pm_loop_sse(body))


@router.post(
    "/pm/loop/stop",
    response_model=PmLoopStopResponse,
    tags=["Medium (PM)"],
    summary="Stop PM background loop",
)
def pm_loop_stop(body: PmLoopRequest):
    return pm_loop.stop_pm_loop(body.profile.name)


@router.get(
    "/pm/loop/status",
    response_model=PmLoopStatusOut,
    tags=["Medium (PM)"],
    summary="PM loop status",
)
def pm_loop_status(profile: str):
    return pm_loop.pm_loop_status(profile)


# ── Short (PS) ───────────────────────────────────────────────────────────────


@router.post(
    "/ps/solve",
    response_model=PsSolveResponse,
    tags=["Short (PS)"],
    summary="Run PS once",
    description=(
        "Single ShortLayer solve using SLAs from the profile's last PL result. "
        "Updates shared demand for the PM loop."
    ),
)
def ps_solve(body: PsLoopRequest):
    try:
        return ps_loop.solve_ps_once(body.profile.name, body.params, cycle=0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/ps/loop/start",
    tags=["Short (PS)"],
    summary="Start PS background loop (SSE)",
    description=(
        "Run ShortLayer on an interval until Stop or max_cycles. Streams "
        "cycle logs as Server-Sent Events."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from PS loop",
        }
    },
)
def ps_loop_start(body: PsLoopRequest):
    return _sse_response(ps_loop.iter_ps_loop_sse(body))


@router.post(
    "/ps/loop/stop",
    response_model=PsLoopStopResponse,
    tags=["Short (PS)"],
    summary="Stop PS background loop",
)
def ps_loop_stop(body: PsLoopRequest):
    return ps_loop.stop_ps_loop(body.profile.name)


@router.get(
    "/ps/loop/status",
    response_model=PsLoopStatusOut,
    tags=["Short (PS)"],
    summary="PS loop status",
)
def ps_loop_status(profile: str):
    return ps_loop.ps_loop_status(profile)
