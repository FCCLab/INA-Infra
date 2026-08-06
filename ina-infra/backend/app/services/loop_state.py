"""Per-profile PM/PS loop state and cancel registry."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.schemas import (
    PmLoopParams,
    PmSolveResponse,
    PsLoopParams,
    PsSolveResponse,
)


@dataclass
class ProfileLoopState:
    demand: Dict[int, float] = field(default_factory=dict)
    pm_running: bool = False
    ps_running: bool = False
    pm_cycle: int = 0
    ps_cycle: int = 0
    last_pm: Optional[PmSolveResponse] = None
    last_ps: Optional[PsSolveResponse] = None
    pm_params: Optional[PmLoopParams] = None
    ps_params: Optional[PsLoopParams] = None


_lock = threading.Lock()
_states: Dict[str, ProfileLoopState] = {}
_pm_cancel: Dict[str, threading.Event] = {}
_ps_cancel: Dict[str, threading.Event] = {}


def _state_unlocked(profile: str) -> ProfileLoopState:
    if profile not in _states:
        _states[profile] = ProfileLoopState()
    return _states[profile]


def get_state(profile: str) -> ProfileLoopState:
    with _lock:
        return _state_unlocked(profile)


def register_pm(profile: str) -> threading.Event:
    ev = threading.Event()
    with _lock:
        old = _pm_cancel.get(profile)
        if old is not None:
            old.set()
        _pm_cancel[profile] = ev
        _state_unlocked(profile).pm_running = True
    return ev


def register_ps(profile: str) -> threading.Event:
    ev = threading.Event()
    with _lock:
        old = _ps_cancel.get(profile)
        if old is not None:
            old.set()
        _ps_cancel[profile] = ev
        _state_unlocked(profile).ps_running = True
    return ev


def unregister_pm(profile: str, ev: threading.Event) -> None:
    with _lock:
        if _pm_cancel.get(profile) is ev:
            _pm_cancel.pop(profile, None)
        _state_unlocked(profile).pm_running = False


def unregister_ps(profile: str, ev: threading.Event) -> None:
    with _lock:
        if _ps_cancel.get(profile) is ev:
            _ps_cancel.pop(profile, None)
        _state_unlocked(profile).ps_running = False


def stop_pm(profile: str) -> bool:
    with _lock:
        ev = _pm_cancel.get(profile)
        if ev is None:
            return False
        ev.set()
    return True


def stop_ps(profile: str) -> bool:
    with _lock:
        ev = _ps_cancel.get(profile)
        if ev is None:
            return False
        ev.set()
    return True


def is_pm_running(profile: str) -> bool:
    with _lock:
        return _state_unlocked(profile).pm_running


def is_ps_running(profile: str) -> bool:
    with _lock:
        return _state_unlocked(profile).ps_running
