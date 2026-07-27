#!/usr/bin/env python3
"""Sample: PlanningLayer takes a list of Slice objects."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ina import Network, PlanningLayer, Slice, make_slices

LOC = {0: "Edge", 1: "Regional", 2: "Central"}


def main() -> None:
    net = Network()
    print(net.format_settings())

    # Each slice carries its own properties
    slices = make_slices(ids=[1, 2, 3], seed=2025)
    # Or build manually:
    # slices = [
    #     Slice(id=1, t_bar=40, d_bar=100, h_s=0, eta_t0=3.0, slice_type="mMTC"),
    #     Slice(id=2, t_bar=40, d_bar=100, h_s=0, eta_t0=2.0, slice_type="mMTC"),
    # ]

    print("\nINPUT slices:")
    for s in slices:
        print(f"  Slice(id={s.id}, t_bar={s.t_bar}, d_bar={s.d_bar}, h_s={s.h_s}, type={s.slice_type})")

    PlanningLayer(net).solve(slices)

    # PL outputs: initial NF locations (CU/UPF/APP) + initial reserved PRBs (b_min).
    # PRBs are radio resources (no DC site); only NFs are placed at Edge/Regional/Central.
    print("\nOUTPUT (placement + initial PRBs):")
    for s in slices:
        cu, upf, app = s.placement
        r = s.resources
        print(
            f"  S{s.id}: placement CU={LOC[cu]} UPF={LOC[upf]} APP={LOC[app]} | "
            f"PRBs b_min={r.b_min:.0f} | "
            f"compute CU_cpu={r.a_c_cu:.2f} UPF_cpu={r.a_c_upf:.2f} APP_cpu={r.a_c_app:.2f}"
        )


if __name__ == "__main__":
    main()
