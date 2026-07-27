"""Unit tests for ShortLayer (PS)."""

from __future__ import annotations

from ina import ShortLayer, make_slices


def test_ps_empty(network):
    assert not ShortLayer(network).solve([]).ok


def test_ps_reads_eta_from_slice(network, slices_123, eta):
    for s in slices_123:
        s.eta = eta.calculate(15)
    ps = ShortLayer(network).solve(slices_123)
    assert set(ps.b_min) == {s.id for s in slices_123}
    assert set(ps.b_ded) == set(ps.b_min) == set(ps.b_max)
    assert sum(ps.b_min.values()) <= network.b_total + 1e-6
    for sid in ps.b_min:
        assert 0 <= ps.b_ded[sid] <= ps.b_min[sid] + 1e-6
        assert abs(ps.b_max[sid] - (ps.b_min[sid] + ps.extra)) < 1e-6


def test_ps_urllc_slice(network, eta):
    slices = make_slices(ids=[4], eta=eta, seed=1)
    slices[0].eta = 10.0
    ps = ShortLayer(network).solve(slices)
    assert ps.b_min[4] >= 0
    # h_s=1 ⇒ all reserved PRBs are dedicated
    assert abs(ps.b_ded[4] - ps.b_min[4]) < 1e-6
