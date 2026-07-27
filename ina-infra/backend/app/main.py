"""INA-Infra FastAPI entrypoint — Swagger at /docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.services import profile_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    path = profile_store.init_db()
    print(f"ina-infra: profile DB at {path}", flush=True)
    yield


app = FastAPI(
    title="INA-Infra API",
    description=(
        "PlanningLayer (PL) REST API for multi-site slice placement. "
        "Profiles persist in SQLite (INA_DB_PATH). "
        "Solve SLAs → Multus IP plan → apply GitOps templates. "
        "PM/PS endpoints are stubs for a later phase."
    ),
    version="0.2.0",
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


@app.get("/")
def root():
    return {
        "service": "ina-infra",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/api/v1/health",
        "profiles": "/api/v1/profiles",
    }
