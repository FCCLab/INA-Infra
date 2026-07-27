"""MCS → PRB spectral efficiency η (Mbps per PRB).

η is used as:
  - Slice.eta_t0 at PL time (planning estimate)
  - Slice.eta at each PS step (real-time channel)
  radio_throughput ≈ (b_min + extra_prbs) × η
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


# mcs_index → (Qm modulation order, code_rate × 1024)
DEFAULT_MCS_TABLE: Dict[int, Tuple[int, int]] = {
    0: (2, 120), 1: (2, 157), 2: (2, 193), 3: (2, 251), 4: (2, 308),
    5: (2, 379), 6: (2, 449), 7: (2, 526), 8: (2, 602), 9: (2, 679),
    10: (4, 340), 11: (4, 378), 12: (4, 434), 13: (4, 490), 14: (4, 553),
    15: (4, 616), 16: (4, 658),
    17: (6, 438), 18: (6, 466), 19: (6, 517), 20: (6, 567), 21: (6, 616),
    22: (6, 666), 23: (6, 719), 24: (6, 772), 25: (6, 822), 26: (6, 873),
    27: (6, 910), 28: (6, 948),
}


@dataclass
class EtaCalculator:
    """Convert MCS index to η in Mbps per PRB (simplified 5G-style model)."""

    mcs_table: Dict[int, Tuple[int, int]] = field(
        default_factory=lambda: dict(DEFAULT_MCS_TABLE)
    )
    mimo_layers: int = 4  # spatial layers
    overhead: float = 0.14  # control/reference signal overhead fraction
    scaling_factor: float = 1.0

    def calculate(self, mcs_index: int) -> float:
        """Return η (Mbps/PRB), or 0 if MCS unknown."""
        if mcs_index not in self.mcs_table:
            return 0.0
        qm, r_1024 = self.mcs_table[mcs_index]
        r = r_1024 / 1024.0  # code rate in [0, 1]
        res_per_sec = 12 * 28000  # subcarriers × symbols/sec (model constant)
        return (
            1e-6
            * self.mimo_layers
            * qm
            * self.scaling_factor
            * r
            * res_per_sec
            * (1 - self.overhead)
        )
