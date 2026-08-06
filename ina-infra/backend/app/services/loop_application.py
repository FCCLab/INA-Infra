"""Application hook for PM/PS loop results (default: log only)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LoopApplicationAdapter(Protocol):
    """Integrate PM/PS outputs with external systems (K8s, FlexRIC, etc.)."""

    def on_pm(
        self,
        profile: str,
        cycle: int,
        resources: Dict[int, Any],
        demand: Dict[int, float],
    ) -> None: ...

    def on_ps(
        self,
        profile: str,
        cycle: int,
        ps_result: Any,
        demand: Dict[int, float],
    ) -> None: ...


class PrintApplicationAdapter:
    """Default hook: structured log / print only — no cluster side effects."""

    def on_pm(
        self,
        profile: str,
        cycle: int,
        resources: Dict[int, Any],
        demand: Dict[int, float],
    ) -> None:
        payload = {
            "layer": "PM",
            "profile": profile,
            "cycle": cycle,
            "demand": demand,
            "resources": {
                sid: {
                    "a_c_cu": r.a_c_cu,
                    "a_c_upf": r.a_c_upf,
                    "a_c_app": r.a_c_app,
                }
                for sid, r in resources.items()
            },
        }
        logger.info("PM hook: %s", json.dumps(payload, default=str))

    def on_ps(
        self,
        profile: str,
        cycle: int,
        ps_result: Any,
        demand: Dict[int, float],
    ) -> None:
        payload = {
            "layer": "PS",
            "profile": profile,
            "cycle": cycle,
            "demand": demand,
            "b_min": dict(ps_result.b_min),
            "b_ded": dict(ps_result.b_ded),
            "b_max": dict(ps_result.b_max),
            "extra": ps_result.extra,
        }
        logger.info("PS hook: %s", json.dumps(payload, default=str))


_default_adapter = PrintApplicationAdapter()


def get_adapter() -> LoopApplicationAdapter:
    return _default_adapter
