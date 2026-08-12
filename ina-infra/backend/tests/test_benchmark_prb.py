"""PRB step math + xApp SD normalize."""

from __future__ import annotations

from app.services import benchmark_prb_run, xapp_prb


def test_prb_steps_includes_max():
    assert benchmark_prb_run.prb_steps(10, 100, 10) == [
        10.0,
        20.0,
        30.0,
        40.0,
        50.0,
        60.0,
        70.0,
        80.0,
        90.0,
        100.0,
    ]
    assert benchmark_prb_run.prb_steps(5, 12, 5) == [5.0, 10.0, 12.0]
    assert benchmark_prb_run.prb_steps(40, 40, 10) == [40.0]


def test_normalize_direction():
    assert benchmark_prb_run.normalize_direction("DL") == "dl"
    assert benchmark_prb_run.normalize_direction("ul") == "ul"
    try:
        benchmark_prb_run.normalize_direction("sideways")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_normalize_sd():
    assert xapp_prb.normalize_sd("0x1") == "0x000001"
    assert xapp_prb.normalize_sd(2) == "0x000002"
    assert xapp_prb.normalize_sd("0x000003") == "0x000003"
