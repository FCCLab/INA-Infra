"""Unit tests for MediumLayer (PM)."""

from __future__ import annotations

import pytest

from ina import MediumLayer, PlanningLayer


def test_pm_empty(network):
    assert MediumLayer(network).solve([]) == {}


def test_pm_uses_slice_demand(network, slices_123):
    pl = PlanningLayer(network).solve(slices_123)
    assert pl.ok
    for s in slices_123:
        s.demand = s.t_bar * 1.2

    pm = MediumLayer(network).solve(slices_123)
    assert set(pm) == {s.id for s in slices_123}
    for s in slices_123:
        assert s.resources.a_c_cu >= 0
        assert s.resources.b_min is None


def test_pm_requires_placement(network, slices_123):
    for s in slices_123:
        s.demand = 40.0
        s.placement = None
    with pytest.raises(ValueError):
        MediumLayer(network).solve(slices_123)
