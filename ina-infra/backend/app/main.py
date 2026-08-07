"""INA-Infra FastAPI entrypoint — Swagger at /docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.routes import router
from app.services import profile_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    path = profile_store.init_db()
    print(f"ina-infra: profile DB at {path}", flush=True)
    yield


OPENAPI_TAGS = [
    {
        "name": "System",
        "description": "Process health and diagnostics.",
    },
    {
        "name": "Network",
        "description": "PlanningLayer substrate model (capacities, PRBs, weights).",
    },
    {
        "name": "Slices",
        "description": "Slice SLA templates used as profile defaults.",
    },
    {
        "name": "Profiles",
        "description": (
            "Saved deployment profiles (name = K8s namespace). "
            "SQLite under `INA_DB_PATH`."
        ),
    },
    {
        "name": "Clusters",
        "description": "Live cluster discovery and per-profile workload status.",
    },
    {
        "name": "Rollout",
        "description": (
            "Staged restart of NFs after Apply "
            "(NRF → UPF → SMF → PFCP → RAN)."
        ),
    },
    {
        "name": "Planning (PL)",
        "description": (
            "PlanningLayer: solve placement + Multus IP plan, "
            "apply/undeploy GitOps to Gitea."
        ),
    },
    {
        "name": "Medium (PM)",
        "description": "Medium-term layer (stub — not implemented yet).",
    },
    {
        "name": "Short (PS)",
        "description": "Short-term PRB layer (stub — not implemented yet).",
    },
    {
        "name": "Operator agents",
        "description": (
            "Connected RAN operator agents (inside oai-ran-operator). "
            "Agents open WebSocket `/api/v1/operators/ws` to declare NFs + controllable "
            "kinds and receive pushed desired compute targets. UI uses HTTP to list "
            "agents and set resources."
        ),
    },
]

app = FastAPI(
    title="INA-Infra API",
    description=(
        "Multi-timescale **INA** control plane for the Nephio lab.\n\n"
        "Typical flow:\n"
        "1. **Profiles** — create/save Multus subnet + slice SLAs\n"
        "2. **Planning (PL)** — `solve` → `apply` (GitOps)\n"
        "3. **Rollout** — staged NF restart / PFCP wait\n"
        "4. **Clusters** — watch Deployment readiness\n\n"
        "**Swagger UI:** [/docs](/docs) · **ReDoc:** [/redoc](/redoc) · "
        "**OpenAPI:** [/openapi.json](/openapi.json)"
    ),
    version="0.2.0",
    openapi_tags=OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", include_in_schema=False)
def root():
    """Send browsers to Swagger UI."""
    return RedirectResponse(url="/docs", status_code=307)


@app.get("/api", include_in_schema=False)
def api_index():
    return {
        "service": "ina-infra",
        "swagger": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "health": "/api/v1/health",
        "profiles": "/api/v1/profiles",
    }
