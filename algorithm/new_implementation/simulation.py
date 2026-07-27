#!/usr/bin/env python3
"""Simulation orchestration using Slice lists (no global config).

Plots match the classic simulation.py figures (throughput, topology,
bottleneck, utilization, dynamic vs static).
"""

from __future__ import annotations

import argparse
import os
import random
from dataclasses import dataclass, field, replace
from typing import Dict, List

import numpy as np

from ina import (
    EtaCalculator,
    MediumLayer,
    Network,
    PlanningLayer,
    ShortLayer,
    Slice,
    SliceResources,
    make_slices,
)
from plots import plot_all


@dataclass
class SimHistory:
    t_actual: Dict[int, List[float]] = field(default_factory=dict)
    violation: Dict[int, List[float]] = field(default_factory=dict)
    t_baseline: Dict[int, List[float]] = field(default_factory=dict)
    vio_baseline: Dict[int, List[float]] = field(default_factory=dict)
    # Per-slice bottleneck series (for fig 3)
    analysis: Dict[int, Dict[str, List[float]]] = field(default_factory=dict)
    # Per-location utilization % (for fig 4)
    usage: Dict[int, Dict[str, List[float]]] = field(default_factory=dict)


class Simulation:
    """Staged PL + nested PM/PS loop."""

    def __init__(
        self,
        network: Network | None = None,
        slices: List[Slice] | None = None,
        activation_schedule: Dict[int, List[int]] | None = None,
        num_pm_cycles: int = 10,
        ps_steps_per_pm: int = 10,
        seed: int = 2025,
        eta: EtaCalculator | None = None,
    ):
        self.network = network or Network()
        self.eta = eta or EtaCalculator()
        self.seed = seed
        self.num_pm_cycles = num_pm_cycles
        self.ps_steps_per_pm = ps_steps_per_pm
        self.activation_schedule = activation_schedule or {
            0: [1, 2, 3],
            3: [4, 5, 6],
            6: [7, 8],
        }
        self.all_slices = slices or make_slices(eta=self.eta, seed=seed)
        self.by_id = {s.id: s for s in self.all_slices}

        self.pl = PlanningLayer(self.network)
        self.pm = MediumLayer(self.network)
        self.ps = ShortLayer(self.network)

        self.history = SimHistory()
        self.deploy_map: Dict[int, tuple] = {}
        self.resources: Dict[int, SliceResources] = {}
        self.baseline: Dict[int, SliceResources] = {}

    def _active_slice_objects(self, active_ids: List[int]) -> List[Slice]:
        return [self.by_id[i] for i in active_ids]

    def _record_usage(self, active: List[Slice]) -> None:
        """Append one PM-cycle worth of utilization samples (× PS steps)."""
        net = self.network
        for j in net.locations:
            app_cpu = app_ram = app_gpu = nf_cpu = nf_ram = 0.0
            for s in active:
                res = self.resources[s.id]
                loc_cu, loc_upf, loc_app = self.deploy_map[s.id]
                if loc_app == j:
                    app_cpu += res.a_c_app
                    app_ram += res.a_r_app
                    app_gpu += res.a_g_app
                if loc_cu == j:
                    nf_cpu += res.a_c_cu
                    nf_ram += res.a_r_cu
                if loc_upf == j:
                    nf_cpu += res.a_c_upf
                    nf_ram += res.a_r_upf
            vals = {
                "APP_CPU": (app_cpu / net.c_a_capacity[j]) * 100 if net.c_a_capacity[j] else 0,
                "APP_RAM": (app_ram / net.r_a_capacity[j]) * 100 if net.r_a_capacity[j] else 0,
                "APP_GPU": (app_gpu / net.g_a_capacity[j]) * 100 if net.g_a_capacity[j] else 0,
                "NF_CPU": (nf_cpu / net.c_n_capacity[j]) * 100 if net.c_n_capacity[j] else 0,
                "NF_RAM": (nf_ram / net.r_n_capacity[j]) * 100 if net.r_n_capacity[j] else 0,
            }
            for _ in range(self.ps_steps_per_pm):
                for k, v in vals.items():
                    self.history.usage[j][k].append(v)

    def run(self) -> SimHistory:
        rng = random.Random(self.seed)
        active_ids: List[int] = []
        ids = [s.id for s in self.all_slices]
        locs = self.network.locations
        self.history = SimHistory(
            t_actual={s: [] for s in ids},
            violation={s: [] for s in ids},
            t_baseline={s: [] for s in ids},
            vio_baseline={s: [] for s in ids},
            analysis={
                s: {"SLA": [], "ComputeCap": [], "RadioGuaranteed": []} for s in ids
            },
            usage={
                j: {k: [] for k in ("APP_CPU", "APP_RAM", "APP_GPU", "NF_CPU", "NF_RAM")}
                for j in locs
            },
        )

        print("Starting Simulation...")
        for pm_cycle in range(self.num_pm_cycles):
            new_ids = self.activation_schedule.get(pm_cycle, [])
            if new_ids:
                active_ids.extend(new_ids)
                active_ids = sorted(set(active_ids))
                active = self._active_slice_objects(active_ids)
                pl = self.pl.solve(active)
                if not pl.ok:
                    raise RuntimeError(f"PL failed at PM cycle {pm_cycle}")
                self.deploy_map = pl.deploy_map
                self.resources.update(pl.resources)
                self.baseline.update({s: replace(r) for s, r in pl.resources.items()})

            active = self._active_slice_objects(active_ids)
            radio_buf = {s.id: [] for s in active}
            compute_cap = {
                s.id: self.resources[s.id].compute_cap(self.network) for s in active
            }
            baseline_cap = {
                s.id: self.baseline[s.id].compute_cap(self.network) for s in active
            }

            self._record_usage(active)

            for _ in range(self.ps_steps_per_pm):
                for s in active:
                    s.eta = self.eta.calculate(rng.randint(5, 28))
                ps = self.ps.solve(active)
                b_min_map = ps.b_min

                reserved_b = sum(self.baseline[s.id].b_min or 0.0 for s in active)
                slack_b = max(0.0, self.network.b_total - reserved_b)
                extra_b = slack_b / len(active) if active else 0.0

                for sid in ids:
                    if sid in active_ids:
                        s = self.by_id[sid]
                        radio = ps.b_max[sid] * s.eta
                        actual = min(radio, compute_cap[sid])
                        vio = max(0.0, s.t_bar - actual)
                        radio_buf[sid].append(radio)
                        radio_g = b_min_map[sid] * s.eta

                        b_static = self.baseline[sid].b_min or 0.0
                        radio_b = (b_static + extra_b) * s.eta
                        actual_b = min(radio_b, baseline_cap[sid])
                        vio_b = max(0.0, s.t_bar - actual_b)

                        self.history.analysis[sid]["SLA"].append(s.t_bar)
                        self.history.analysis[sid]["ComputeCap"].append(compute_cap[sid])
                        self.history.analysis[sid]["RadioGuaranteed"].append(radio_g)
                    else:
                        actual = vio = actual_b = vio_b = 0.0
                        self.history.analysis[sid]["SLA"].append(0.0)
                        self.history.analysis[sid]["ComputeCap"].append(0.0)
                        self.history.analysis[sid]["RadioGuaranteed"].append(0.0)

                    self.history.t_actual[sid].append(actual)
                    self.history.violation[sid].append(vio)
                    self.history.t_baseline[sid].append(actual_b)
                    self.history.vio_baseline[sid].append(vio_b)

            if active:
                for s in active:
                    s.demand = float(np.mean(radio_buf[s.id]))
                updated = self.pm.solve(active, self.deploy_map)
                if updated:
                    self.resources = updated

            print(f"PM Cycle {pm_cycle} finished.")

        return self.history

    def plot(self, show: bool = True, save_dir: str | None = "sim_output") -> None:
        """Show / save the same five figures as the original simulation.py."""
        plot_all(
            self.history,
            self.all_slices,
            self.deploy_map,
            self.num_pm_cycles,
            self.ps_steps_per_pm,
            self.activation_schedule,
            show=show,
            save_dir=save_dir,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="INA-Infra multi-timescale simulation")
    parser.add_argument("--no-show", action="store_true", help="Do not open GUI windows")
    parser.add_argument(
        "--save-dir", default="sim_output",
        help="Directory for PNG figures (empty string to skip saving)",
    )
    args = parser.parse_args()

    # Headless-friendly default when no display
    if args.no_show or not os.environ.get("DISPLAY"):
        import matplotlib
        matplotlib.use("Agg")

    sim = Simulation()
    hist = sim.run()
    steps = sim.num_pm_cycles * sim.ps_steps_per_pm
    avg_vio = sum(sum(v) for v in hist.violation.values()) / steps
    avg_thr = sum(sum(v) for v in hist.t_actual.values()) / steps
    print(f"Done. steps={steps} avg_throughput={avg_thr:.2f} avg_violation={avg_vio:.2f}")
    print("Deploy map:", sim.deploy_map)

    save_dir = args.save_dir or None
    sim.plot(show=not args.no_show and bool(os.environ.get("DISPLAY")), save_dir=save_dir)


if __name__ == "__main__":
    main()
