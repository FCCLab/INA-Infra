"""Helpers to build lists of Slice objects."""

from __future__ import annotations

import random
from typing import Iterable, List, Optional

from ina.eta import EtaCalculator
from ina.models import Slice


def default_slice_specs() -> List[tuple]:
    """(id, t_bar, d_bar, h_s, type)."""
    return [
        (1, 40, 100, 0, "mMTC"),
        (2, 40, 100, 0, "mMTC"),
        (3, 40, 30, 0, "mMTC(Reg)"),
        (4, 60, 20, 1, "URLLC(Split)"),
        (5, 60, 10, 1, "URLLC(Edge)"),
        (6, 100, 15, 0, "eMBB(Edge)"),
        (7, 100, 35, 0, "eMBB"),
        (8, 100, 35, 0, "eMBB"),
    ]


def make_slices(
    ids: Optional[Iterable[int]] = None,
    eta: Optional[EtaCalculator] = None,
    seed: int = 2025,
) -> List[Slice]:
    """Return a list of Slice objects with SLA properties filled in."""
    eta = eta or EtaCalculator()
    rng = random.Random(seed)
    wanted = set(ids) if ids is not None else None
    out: List[Slice] = []
    for sid, t_bar, d_bar, h_s, stype in default_slice_specs():
        if wanted is not None and sid not in wanted:
            continue
        out.append(
            Slice(
                id=sid,
                t_bar=t_bar,
                d_bar=d_bar,
                h_s=h_s,
                eta_t0=eta.calculate(rng.randint(10, 20)),
                slice_type=stype,
            )
        )
    return out
