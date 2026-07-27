#!/usr/bin/env python3
"""Sample: MediumLayer reads demand from each Slice.demand."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ina import MediumLayer, Network, PlanningLayer, make_slices


def main() -> None:
    net = Network()
    print(net.format_settings())

    slices = make_slices(ids=[1, 2, 3], seed=2025)
    pl = PlanningLayer(net).solve(slices)
    assert pl.ok

    # Set demand on each slice object
    for s, d in zip(slices, [45.0, 42.0, 50.0]):
        s.demand = d

    print("\nINPUT slices (with demand):")
    for s in slices:
        print(f"  S{s.id}: placement={s.placement} demand={s.demand}")

    MediumLayer(net).solve(slices)
    print("\nOUTPUT compute:")
    for s in slices:
        print(f"  S{s.id}: CU_cpu={s.resources.a_c_cu:.2f} APP_cpu={s.resources.a_c_app:.2f}")


if __name__ == "__main__":
    main()
