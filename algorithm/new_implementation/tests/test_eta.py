"""Unit tests for η calculator (no Gurobi)."""

from __future__ import annotations

from ina import EtaCalculator


def test_eta_known_mcs_positive():
    eta = EtaCalculator()
    assert eta.calculate(15) > 0
    assert eta.calculate(0) > 0


def test_eta_invalid_mcs_zero():
    assert EtaCalculator().calculate(999) == 0.0


def test_eta_higher_mcs_not_always_lower():
    eta = EtaCalculator()
    # MCS 15 (64QAM-class table entry) should exceed low MCS
    assert eta.calculate(15) > eta.calculate(1)


def test_eta_scales_with_mimo():
    e2 = EtaCalculator(mimo_layers=2).calculate(15)
    e4 = EtaCalculator(mimo_layers=4).calculate(15)
    assert abs(e4 - 2 * e2) < 1e-9
