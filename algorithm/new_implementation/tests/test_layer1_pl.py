"""Unit tests for PlanningLayer (PL)."""

from __future__ import annotations

from ina import PlanningLayer, Slice, make_slices


def test_pl_empty(network):
    result = PlanningLayer(network).solve([])
    assert not result.ok


def test_pl_accepts_slice_list(network, slices_123):
    result = PlanningLayer(network).solve(slices_123)
    assert result.ok
    for s in slices_123:
        assert s.placement is not None
        assert s.resources is not None
        assert s.resources.b_min is not None


def test_pl_manual_slices(network):
    slices = [
        Slice(id=1, t_bar=40, d_bar=100, h_s=0, eta_t0=3.0, slice_type="mMTC"),
        Slice(id=2, t_bar=40, d_bar=100, h_s=0, eta_t0=2.0, slice_type="mMTC"),
    ]
    result = PlanningLayer(network).solve(slices)
    assert result.ok
    assert set(result.deploy_map) == {1, 2}


def test_pl_prb_within_budget(network, slices_123):
    result = PlanningLayer(network).solve(slices_123)
    total = sum(r.b_min for r in result.resources.values())
    assert total <= network.b_total + 1e-6
