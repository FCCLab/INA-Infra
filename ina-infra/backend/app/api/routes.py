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
    SliceIn,
)
from app.services import gitea_apply, pl_solver

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health():
    return {"status": "ok", "service": "ina-infra"}


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
