"""REST routes for INA-Infra (grouped OpenAPI tags)."""

from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.schemas import (
    AppDeployRequest,
    AppDeployResponse,
    AppUndeployRequest,
    AppUndeployResponse,
    BenchmarkDeployRequest,
    BenchmarkPrbApplyRequest,
    BenchmarkPrbApplyResponse,
    BenchmarkPrbRunRequest,
    BenchmarkPrbRunStatusOut,
    BenchmarkPrbSlicesOut,
    BenchmarkPrbSliceOut,
    BenchmarkPrbStopResponse,
    BenchmarkRunRequest,
    BenchmarkRunStatusOut,
    BenchmarkRunStopResponse,
    BenchmarkTrafficOut,
    BenchmarkTrafficRequest,
    BenchmarkUndeployRequest,
    UeApplyReport,
    UeDeclare,
    UeDesiredOut,
    UeDesiredRequest,
    UeListOut,
    UeOut,
    OperatorApplyReport,
    OperatorCpuSetRequest,
    OperatorResourceSetRequest,
    OperatorDesiredOut,
    OperatorListOut,
    OperatorOut,
    OperatorRegisterRequest,
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
    UeClientStatusOut,
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
    OaiRegistryStatusResponse,
    PsSolveResponse,
    SliceApplicationConfig,
    SliceIn,
)
from app.services import (
    application_deploy,
    benchmark,
    benchmark_log,
    benchmark_prb_run,
    benchmark_run,
    benchmark_traffic,
    cluster_status,
    operators,
    ues,
    gitea_apply,
    pl_solver,
    pm_loop,
    profile_rollout,
    profile_store,
    ps_loop,
    registry_service,
    influx_service,
    xapp_prb,
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
    "/registry/oai-images",
    response_model=OaiRegistryStatusResponse,
    tags=["Profiles"],
    summary="Query private registry for OAI container images",
    description="Inspects the private Docker registry (10.1.132.30:5000) for available tags and latest resolved images.",
)
def get_oai_registry_images(refresh: bool = False):
    return registry_service.get_oai_registry_status(force_refresh=refresh)


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


# ── Applications ─────────────────────────────────────────────────────────────


@router.get(
    "/profiles/{name}/applications",
    response_model=Dict[str, SliceApplicationConfig],
    tags=["Applications"],
    summary="Get slice application configs for a profile",
)
def get_profile_applications(name: str):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    return rec.applications or profile_store.default_applications(rec.slices)


@router.put(
    "/profiles/{name}/applications",
    response_model=ProfileRecord,
    tags=["Applications"],
    summary="Save slice application configs for a profile",
)
def save_profile_applications(name: str, body: Dict[str, SliceApplicationConfig]):
    try:
        return profile_store.save_profile_applications(name, body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/profiles/{name}/applications/deploy",
    response_model=AppDeployResponse,
    tags=["Applications"],
    summary="Deploy slice application client UE(s)",
)
def deploy_applications(name: str, body: AppDeployRequest):
    gen = application_deploy.deploy_application_stream(
        name, slice_id=body.slice_id, config=body.config, applications=body.applications
    )
    last_res = None
    for chunk in gen:
        if chunk.startswith("event: result\n"):
            payload = chunk.split("data: ", 1)[1].strip()
            last_res = json.loads(payload)
        elif chunk.startswith("event: error\n"):
            payload = chunk.split("data: ", 1)[1].strip()
            err_obj = json.loads(payload)
            raise HTTPException(
                status_code=400, detail=err_obj.get("message", "Deploy failed")
            )
    if last_res is not None:
        return AppDeployResponse.model_validate(last_res)
    raise HTTPException(status_code=500, detail="Deploy stream ended without result")


@router.post(
    "/profiles/{name}/applications/deploy/stream",
    tags=["Applications"],
    summary="Deploy slice application client UE(s) (SSE stream)",
)
def deploy_applications_stream(name: str, body: AppDeployRequest):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    return _sse_response(
        application_deploy.deploy_application_stream(
            name, slice_id=body.slice_id, config=body.config, applications=body.applications
        )
    )


@router.post(
    "/profiles/{name}/applications/undeploy",
    response_model=AppUndeployResponse,
    tags=["Applications"],
    summary="Undeploy slice application client UE(s)",
)
def undeploy_applications(name: str, body: AppUndeployRequest):
    gen = application_deploy.undeploy_application_stream(name, slice_id=body.slice_id)
    last_res = None
    for chunk in gen:
        if chunk.startswith("event: result\n"):
            payload = chunk.split("data: ", 1)[1].strip()
            last_res = json.loads(payload)
        elif chunk.startswith("event: error\n"):
            payload = chunk.split("data: ", 1)[1].strip()
            err_obj = json.loads(payload)
            raise HTTPException(
                status_code=400, detail=err_obj.get("message", "Undeploy failed")
            )
    if last_res is not None:
        return AppUndeployResponse.model_validate(last_res)
    raise HTTPException(status_code=500, detail="Undeploy stream ended without result")


@router.post(
    "/profiles/{name}/applications/undeploy/stream",
    tags=["Applications"],
    summary="Undeploy slice application client UE(s) (SSE stream)",
)
def undeploy_applications_stream(name: str, body: AppUndeployRequest):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    return _sse_response(
        application_deploy.undeploy_application_stream(name, slice_id=body.slice_id)
    )


@router.get(
    "/influx/status",
    tags=["InfluxDB"],
    summary="Check InfluxDB connection status",
)
def get_influx_status():
    return influx_service.check_health()


@router.get(
    "/profiles/{name}/applications/metrics",
    tags=["Applications"],
    summary="Query recent application metrics from InfluxDB",
)
def get_application_metrics(
    name: str,
    slice_id: Optional[int] = None,
    range_s: int = 300,
):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    return {
        "ok": True,
        "profile": name,
        "slice_id": slice_id,
        "range_s": range_s,
        "metrics": influx_service.query_application_metrics(
            name, slice_id=slice_id, range_s=range_s
        ),
    }


@router.post(
    "/profiles/{name}/applications/metrics/push",
    tags=["Applications"],
    summary="Push application metrics point to InfluxDB",
)
def push_application_metrics(
    name: str,
    payload: Dict[str, Any],
):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")

    fields = payload.get("fields") or {}
    tags = payload.get("tags") or {}
    tags["profile"] = name
    if "slice" in payload and "slice" not in tags:
        tags["slice"] = str(payload["slice"])
    if "app_type" in payload and "app_type" not in tags:
        tags["app_type"] = str(payload["app_type"])

    ok = influx_service.write_point(fields=fields, tags=tags)
    return {"ok": ok, "profile": name, "tags": tags, "fields": fields}


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


@router.get(
    "/profiles/{name}/ue-client-status",
    response_model=UeClientStatusOut,
    tags=["Applications"],
    summary="Live edge status of on-demand client UE Deployments",
)
def get_profile_ue_client_status(name: str):
    rec = profile_store.get_profile(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {name}")
    try:
        raw = cluster_status.profile_ue_client_status(name)
        return UeClientStatusOut.model_validate(raw)
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


# ── Benchmark (oai-benchmark) ─────────────────────────────────────────────────


@router.post(
    "/benchmark/deploy/stream",
    tags=["Benchmark"],
    summary="Deploy oai-benchmark GitOps (SSE)",
    description=(
        "Runs `scripts/render_oai_benchmark_gitops.sh` (5GC CP on **central**; "
        "on **edge**, GitOps deploys `oai-ran-operator` + NFDeployments so the "
        "operator create-once CU-CP/CU-UP/DU; UPF+UE stay static). Then pushes "
        "to Gitea and best-effort pins Multus/nodeSelectors. "
        "`dry_run` renders without push."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from render + git push",
        }
    },
)
def benchmark_deploy_stream(body: BenchmarkDeployRequest):
    benchmark_log.write(
        f"deploy start dry_run={body.dry_run} log={benchmark_log.log_path()}",
        source="deploy",
    )
    return _sse_response(benchmark_log.tee_sse("deploy", benchmark.iter_deploy_sse(body)))


@router.post(
    "/benchmark/undeploy/stream",
    tags=["Benchmark"],
    summary="Undeploy oai-benchmark GitOps (SSE)",
    description=(
        "`dry_run=true` (Clear): remove local `namespaces/oai-benchmark/`; "
        "skip push. `dry_run=false` (Undeploy): clear + push + force-delete "
        "namespaces on central/edge."
    ),
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE log stream from undeploy",
        }
    },
)
def benchmark_undeploy_stream(body: BenchmarkUndeployRequest):
    benchmark_log.write(
        f"undeploy start dry_run={body.dry_run} log={benchmark_log.log_path()}",
        source="undeploy",
    )
    return _sse_response(
        benchmark_log.tee_sse("undeploy", benchmark.iter_undeploy_sse(body))
    )


@router.post(
    "/benchmark/run/start",
    response_model=BenchmarkRunStatusOut,
    tags=["Benchmark"],
    summary="Start oai-benchmark CPU sweep",
    description=(
        "Linear CPU steps from min_cpu to max_cpu on the RAN operator NF "
        "(default oai-cu-up). Each step: apply CPU → warmup → measure window. "
        "Throughput is stored in SQLite; this API returns start/stop times."
    ),
)
def benchmark_run_start(body: BenchmarkRunRequest):
    try:
        return benchmark_run.start_run(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/benchmark/run/stop",
    response_model=BenchmarkRunStopResponse,
    tags=["Benchmark"],
    summary="Stop oai-benchmark CPU sweep",
)
def benchmark_run_stop():
    return benchmark_run.stop_run()


@router.get(
    "/benchmark/run/status",
    response_model=BenchmarkRunStatusOut,
    tags=["Benchmark"],
    summary="oai-benchmark CPU sweep status + step list",
)
def benchmark_run_status():
    return benchmark_run.status()


@router.get(
    "/benchmark/traffic",
    response_model=BenchmarkTrafficOut,
    tags=["Benchmark"],
    summary="Get desired UE iperf traffic type (UDP/TCP)",
    description="Alias of global desired PROTOCOL from the UE WebSocket control plane.",
)
def benchmark_traffic_get():
    return benchmark_traffic.get_traffic()


@router.post(
    "/benchmark/traffic",
    response_model=BenchmarkTrafficOut,
    tags=["Benchmark"],
    summary="Set UE iperf traffic type (UDP/TCP)",
    description=(
        "Broadcasts PROTOCOL via WebSocket ``desired`` to connected UE "
        "iperf3-clients (no pod restart). Prefer ``POST /ues/desired``."
    ),
)
def benchmark_traffic_set(body: BenchmarkTrafficRequest):
    try:
        return benchmark_traffic.set_traffic(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/ues",
    response_model=UeListOut,
    tags=["UE agents"],
    summary="List connected UE iperf agents",
)
def ues_list():
    return ues.list_ues()


@router.post(
    "/ues/desired",
    response_model=UeDesiredOut,
    tags=["UE agents"],
    summary="Set desired iperf params for one UE",
    description=(
        "Updates per-UE desired (protocol/action/bandwidth/…) and pushes a "
        "``desired`` frame on that agent's `/ues/ws` session. ``id`` is required."
    ),
)
def ues_set_desired(body: UeDesiredRequest):
    try:
        return ues.set_desired(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"UE agent not found: {exc}") from exc


@router.get(
    "/ues/{ue_id}",
    response_model=UeOut,
    tags=["UE agents"],
    summary="Get one UE iperf agent",
)
def ues_get(ue_id: str):
    try:
        return ues.get_ue(ue_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/ues/{ue_id}",
    tags=["UE agents"],
    summary="Drop a UE agent from the registry",
)
def ues_delete(ue_id: str):
    if not ues.delete_ue(ue_id):
        raise HTTPException(status_code=404, detail=f"UE agent not found: {ue_id}")
    return {"ok": True, "id": ue_id}


@router.websocket("/ues/ws")
async def ues_ws(websocket: WebSocket):
    """Persistent UE iperf channel: declare, status, apply_report; receive desired."""
    await websocket.accept()
    ue_id: Optional[str] = None
    out_q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    writer_task: Optional[asyncio.Task] = None

    async def writer() -> None:
        while True:
            msg = await out_q.get()
            await websocket.send_json(msg)

    try:
        writer_task = asyncio.create_task(writer())
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await out_q.put({"type": "error", "message": "invalid JSON"})
                continue
            if not isinstance(data, dict):
                await out_q.put(
                    {"type": "error", "message": "message must be a JSON object"}
                )
                continue
            msg_type = str(data.get("type") or "").strip().lower()

            if msg_type in ("hello", "declare"):
                try:
                    body = UeDeclare.model_validate(
                        {k: v for k, v in data.items() if k != "type"}
                    )
                except ValidationError as exc:
                    await out_q.put({"type": "error", "message": str(exc)})
                    continue
                if ue_id and ue_id != body.id:
                    await out_q.put(
                        {
                            "type": "error",
                            "message": f"ue id changed mid-session ({ue_id} → {body.id})",
                        }
                    )
                    continue
                first = ue_id is None
                if first:
                    ue_id = body.id
                    ues.attach_ws(ue_id, loop, out_q)
                    await out_q.put({"type": "welcome", "id": ue_id})
                ues.register(body)
                continue

            if msg_type == "status":
                if not ue_id:
                    await out_q.put({"type": "error", "message": "declare before status"})
                    continue
                try:
                    ues.report_status(ue_id, data)
                except KeyError:
                    await out_q.put(
                        {"type": "error", "message": f"UE agent not found: {ue_id}"}
                    )
                except ValueError as exc:
                    await out_q.put({"type": "error", "message": str(exc)})
                continue

            if msg_type == "apply_report":
                if not ue_id:
                    await out_q.put(
                        {"type": "error", "message": "declare before apply_report"}
                    )
                    continue
                try:
                    report = UeApplyReport.model_validate(
                        {k: v for k, v in data.items() if k != "type"}
                    )
                except ValidationError as exc:
                    await out_q.put({"type": "error", "message": str(exc)})
                    continue
                try:
                    ues.report_apply(ue_id, report)
                except KeyError:
                    await out_q.put(
                        {"type": "error", "message": f"UE agent not found: {ue_id}"}
                    )
                continue

            if msg_type == "ping":
                await out_q.put({"type": "pong"})
                continue

            await out_q.put(
                {"type": "error", "message": f"unknown type: {msg_type or '(empty)'}"}
            )
    except WebSocketDisconnect:
        pass
    finally:
        if ue_id is not None:
            ues.detach_ws(ue_id, out_q)
        if writer_task is not None:
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass


@router.get(
    "/benchmark/prb/slices",
    response_model=BenchmarkPrbSlicesOut,
    tags=["Benchmark"],
    summary="GET NS PRB policy from near-RT RIC xApp",
)
def benchmark_prb_slices():
    try:
        raw = xapp_prb.get_slices()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    slices = [
        BenchmarkPrbSliceOut(
            sst=int(s.get("sst") or 0),
            sd=str(s.get("sd") or ""),
            direction=str(s.get("direction") or ""),
            dedicated=float(s.get("dedicated") or 0),
            min=float(s.get("min") or 0),
            max=float(s.get("max") or 0),
        )
        for s in (raw.get("slices") or [])
    ]
    return BenchmarkPrbSlicesOut(
        ok=True,
        xapp_url=xapp_prb.xapp_base_url(),
        tstamp=raw.get("tstamp"),
        indications=raw.get("indications"),
        slices=slices,
    )


@router.post(
    "/benchmark/prb/apply",
    response_model=BenchmarkPrbApplyResponse,
    tags=["Benchmark"],
    summary="PATCH dedicated/min/max PRB via near-RT RIC xApp",
)
def benchmark_prb_apply(body: BenchmarkPrbApplyRequest):
    try:
        direction = benchmark_prb_run.normalize_direction(body.direction)
        raw = xapp_prb.set_prb(
            sst=body.sst,
            sd=body.sd,
            direction=direction,
            dedicated=body.dedicated,
            min_prb=body.min_prb,
            max_prb=body.max_prb,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    traffic_note = ""
    try:
        traffic_note = benchmark_prb_run.ensure_traffic_direction(direction)
    except Exception as exc:  # noqa: BLE001
        traffic_note = f"traffic sync failed: {exc}"
    benchmark_log.write(
        f"prb apply sst={body.sst} sd={body.sd} dir={direction} "
        f"d/m/M={body.dedicated:g}/{body.min_prb:g}/{body.max_prb:g} "
        f"({traffic_note})",
        source="prb",
    )
    return BenchmarkPrbApplyResponse(
        ok=True,
        message=(
            "PATCH sent to xApp (confirm via GET /benchmark/prb/slices); "
            f"traffic: {traffic_note}"
        ),
        applied=BenchmarkPrbSliceOut(
            sst=body.sst,
            sd=xapp_prb.normalize_sd(body.sd),
            direction=direction,
            dedicated=body.dedicated,
            min=body.min_prb,
            max=body.max_prb,
        ),
        raw=raw if isinstance(raw, dict) else None,
    )


@router.post(
    "/benchmark/prb/run/start",
    response_model=BenchmarkPrbRunStatusOut,
    tags=["Benchmark"],
    summary="Start PRB max% sweep via near-RT RIC xApp",
)
def benchmark_prb_run_start(body: BenchmarkPrbRunRequest):
    try:
        return benchmark_prb_run.start_run(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/benchmark/prb/run/stop",
    response_model=BenchmarkPrbStopResponse,
    tags=["Benchmark"],
    summary="Stop PRB sweep",
)
def benchmark_prb_run_stop():
    return benchmark_prb_run.stop_run()


@router.get(
    "/benchmark/prb/run/status",
    response_model=BenchmarkPrbRunStatusOut,
    tags=["Benchmark"],
    summary="PRB sweep status + step list",
)
def benchmark_prb_run_status():
    return benchmark_prb_run.status()


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


# ── Operator agents ───────────────────────────────────────────────────────────


@router.websocket("/operators/ws")
async def operators_ws(websocket: WebSocket):
    """Persistent agent channel: declare NFs, receive desired pushes, apply-report."""
    await websocket.accept()
    operator_id: Optional[str] = None
    out_q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    writer_task: Optional[asyncio.Task] = None

    async def writer() -> None:
        while True:
            msg = await out_q.get()
            await websocket.send_json(msg)

    try:
        writer_task = asyncio.create_task(writer())
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await out_q.put({"type": "error", "message": "invalid JSON"})
                continue
            if not isinstance(data, dict):
                await out_q.put({"type": "error", "message": "message must be a JSON object"})
                continue
            msg_type = str(data.get("type") or "").strip().lower()

            if msg_type in ("hello", "declare"):
                try:
                    body = OperatorRegisterRequest.model_validate(
                        {k: v for k, v in data.items() if k != "type"}
                    )
                except ValidationError as exc:
                    await out_q.put({"type": "error", "message": str(exc)})
                    continue
                if operator_id and operator_id != body.id:
                    await out_q.put(
                        {
                            "type": "error",
                            "message": f"operator id changed mid-session ({operator_id} → {body.id})",
                        }
                    )
                    continue
                first_declare = operator_id is None
                if first_declare:
                    operator_id = body.id
                    operators.attach_ws(operator_id, loop, out_q)
                    await out_q.put({"type": "welcome", "id": operator_id})
                # First declare of the session: seed desired from live reported
                # values so reconnect does not replay stale UI targets.
                operators.register(
                    body, seed_desired_from_reported=first_declare
                )
                continue

            if msg_type == "apply_report":
                if not operator_id:
                    await out_q.put({"type": "error", "message": "declare before apply_report"})
                    continue
                try:
                    report = OperatorApplyReport.model_validate(
                        {k: v for k, v in data.items() if k != "type"}
                    )
                except ValidationError as exc:
                    await out_q.put({"type": "error", "message": str(exc)})
                    continue
                try:
                    operators.report_apply(operator_id, report)
                except KeyError:
                    await out_q.put(
                        {"type": "error", "message": f"operator agent not found: {operator_id}"}
                    )
                continue

            if msg_type == "ping":
                await out_q.put({"type": "pong"})
                continue

            await out_q.put({"type": "error", "message": f"unknown type: {msg_type or '(empty)'}"})
    except WebSocketDisconnect:
        pass
    finally:
        if operator_id is not None:
            operators.detach_ws(operator_id, out_q)
        if writer_task is not None:
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass


@router.post(
    "/operators/register",
    response_model=OperatorOut,
    tags=["Operator agents"],
    summary="Register / declare NFs (HTTP back-compat)",
    deprecated=True,
    description=(
        "Prefer WebSocket `/operators/ws` with `type=declare`. "
        "HTTP remains for tests and tooling."
    ),
)
def operators_register(body: OperatorRegisterRequest):
    return operators.register(body)


@router.get(
    "/operators",
    response_model=OperatorListOut,
    tags=["Operator agents"],
    summary="List connected operator agents",
)
def operators_list():
    return operators.list_operators()


@router.get(
    "/operators/{operator_id}",
    response_model=OperatorOut,
    tags=["Operator agents"],
    summary="Get one operator agent",
)
def operators_get(operator_id: str):
    try:
        return operators.get_operator(operator_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"operator agent not found: {operator_id}")


@router.delete(
    "/operators/{operator_id}",
    tags=["Operator agents"],
    summary="Forget an operator agent registration",
)
def operators_delete(operator_id: str):
    if not operators.delete_operator(operator_id):
        raise HTTPException(status_code=404, detail=f"operator agent not found: {operator_id}")
    return {"ok": True, "id": operator_id}


@router.get(
    "/operators/{operator_id}/desired",
    response_model=OperatorDesiredOut,
    tags=["Operator agents"],
    summary="Desired compute resource targets (HTTP back-compat)",
    deprecated=True,
    description="Prefer WebSocket `desired` pushes on `/operators/ws`.",
)
def operators_desired(operator_id: str):
    try:
        return operators.desired(operator_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"operator agent not found: {operator_id}")


@router.put(
    "/operators/{operator_id}/nfs/{nf}/resources",
    response_model=OperatorOut,
    tags=["Operator agents"],
    summary="Set desired compute resources for an NF",
    description=(
        "UI / planner sets desired compute for an NF. Only kinds listed in that NF's "
        "`controllable` (from the agent declare payload) are accepted. "
        "Pushes an updated `desired` frame to the agent's WebSocket when connected."
    ),
)
def operators_set_resources(operator_id: str, nf: str, body: OperatorResourceSetRequest):
    try:
        return operators.set_resources(operator_id, nf, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"operator agent not found: {operator_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/operators/{operator_id}/nfs/{nf}/cpu",
    response_model=OperatorOut,
    tags=["Operator agents"],
    summary="Set desired compute resources for an NF (alias)",
    description="Back-compat alias for PUT .../resources.",
    deprecated=True,
)
def operators_set_cpu(operator_id: str, nf: str, body: OperatorCpuSetRequest):
    try:
        return operators.set_resources(operator_id, nf, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"operator agent not found: {operator_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/operators/{operator_id}/apply-report",
    response_model=OperatorOut,
    tags=["Operator agents"],
    summary="Operator agent reports resource apply result (HTTP back-compat)",
    deprecated=True,
    description="Prefer WebSocket `type=apply_report` on `/operators/ws`.",
)
def operators_apply_report(operator_id: str, body: OperatorApplyReport):
    try:
        return operators.report_apply(operator_id, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"operator agent not found: {operator_id}")
