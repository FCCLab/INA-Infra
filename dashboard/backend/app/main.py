"""Cluster Dashboard FastAPI entrypoint — Swagger at /docs."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.routes import router

OPENAPI_TAGS = [
    {
        "name": "System",
        "description": "Process health.",
    },
    {
        "name": "Clusters",
        "description": "Live inventory across mgmt / central / regional / edge.",
    },
    {
        "name": "Topology",
        "description": "Graph payload for the React Flow multi-cluster map.",
    },
]

app = FastAPI(
    title="Cluster Dashboard API",
    description=(
        "Multi-cluster ops API for the Nephio network-slicing lab.\n\n"
        "Reads kubeconfigs for **mgmt**, **central**, **regional**, and **edge**, "
        "queries each cluster's **Prometheus** (NodePort 30909) for usage metrics, "
        "and exposes REST inventory + topology for the React UI.\n\n"
        "**Swagger UI:** [/docs](/docs) · **ReDoc:** [/redoc](/redoc) · "
        "**OpenAPI:** [/openapi.json](/openapi.json)"
    ),
    version="0.1.0",
    openapi_tags=OPENAPI_TAGS,
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
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")
