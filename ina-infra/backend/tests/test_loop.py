"""Tests for PM/PS loop state and PL context loading."""

from __future__ import annotations

import pytest

from app.services.loop_common import load_pl_context
from app.services.loop_state import get_state, is_pm_running, stop_pm
from app.services import profile_store


def test_load_pl_context_missing_profile():
    with pytest.raises(ValueError, match="not found"):
        load_pl_context("nonexistent-profile-xyz")


def test_loop_state_defaults():
    st = get_state("test-profile-loop")
    assert st.pm_running is False
    assert st.ps_running is False
    assert st.demand == {}


def test_stop_pm_when_not_running():
    assert stop_pm("test-profile-loop") is False
    assert is_pm_running("test-profile-loop") is False


def _ina_has_pl() -> bool:
    rec = profile_store.get_profile("ina-infra")
    return rec is not None and rec.pl_result is not None and rec.pl_result.ok


@pytest.mark.skipif(not _ina_has_pl(), reason="needs ina-infra profile with PL result")
def test_load_pl_context_ina_infra():
    rec = profile_store.get_profile("ina-infra")
    assert rec is not None
    ctx = load_pl_context("ina-infra")
    assert len(ctx.slices) == len(rec.pl_result.slices)  # type: ignore[union-attr]
    assert len(ctx.deploy_map) == len(ctx.slices)
