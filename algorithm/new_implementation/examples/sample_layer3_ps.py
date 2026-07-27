#!/usr/bin/env python3
"""Sample: ShortLayer reads η from each Slice.eta."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ina import EtaCalculator, Network, ShortLayer, make_slices


def main() -> None:
    net = Network()
    print(net.format_settings())

    eta_calc = EtaCalculator()
    slices = make_slices(ids=[1, 2, 3], seed=2025)
    for s in slices:
        s.eta = eta_calc.calculate(15)

    print("\nINPUT slices:")
    for s in slices:
        print(f"  S{s.id}: t_bar={s.t_bar} h_s={s.h_s} eta={s.eta:.4f}")

    ps = ShortLayer(net).solve(slices)

    print(
        "\nOUTPUT PRBs "
        "(b_min reserved, b_ded dedicated, b_max=b_min+extra usable ceiling):"
    )
    print(f"  shared leftover extra={ps.extra:.1f} PRBs/slice")
    for s in slices:
        b_min = ps.b_min[s.id]
        b_ded = ps.b_ded[s.id]
        b_max = ps.b_max[s.id]
        radio = b_max * s.eta
        print(
            f"  S{s.id}: b_min={b_min:.0f}  b_ded={b_ded:.0f}  "
            f"b_max={b_max:.1f}  radio≈{radio:.2f} Mbps"
        )
    sum_min = sum(ps.b_min.values())
    sum_ded = sum(ps.b_ded.values())
    sum_max = sum(ps.b_max.values())
    print(
        f"  SUM: b_min={sum_min:.0f}  b_ded={sum_ded:.0f}  b_max={sum_max:.1f}  "
        f"(cell b_total={net.b_total})"
    )


if __name__ == "__main__":
    main()
