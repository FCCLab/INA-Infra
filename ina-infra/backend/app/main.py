"""INA-Infra FastAPI entrypoint — Swagger at /docs."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="INA-Infra API",
    description=(
        "PlanningLayer (PL) REST API for multi-site slice placement. "
        "Solve SLAs → view CU/UPF/APP placement → push planning intent to Gitea. "
        "PM/PS endpoints are stubs for a later phase."
    ),
    version="0.1.0",
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
    }
