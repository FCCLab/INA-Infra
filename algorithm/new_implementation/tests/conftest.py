"""Pytest fixtures and path setup."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ina import EtaCalculator, Network, make_slices


@pytest.fixture
def network() -> Network:
    return Network(gurobi_output=0)


@pytest.fixture
def eta() -> EtaCalculator:
    return EtaCalculator()


@pytest.fixture
def slices_123(eta):
    return make_slices(ids=[1, 2, 3], eta=eta, seed=2025)
