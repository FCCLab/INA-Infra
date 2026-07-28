"""Run staged profile namespace rollout (UPF→SMF→PFCP→CU-CP→CU-UP→DU→UEs)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, Iterator, Optional

from app.schemas import (
    ProfileRolloutRequest,
    ProfileRolloutResponse,
    ProfileRolloutStopResponse,
)
from app.services.cmd_stream import (
    CmdResult,
    log_event,
    result_event,
    status_event,
    stream_cmd,
)

# Active rollouts keyed by profile name (one at a time per profile).
_lock = threading.Lock()
_cancel_events: Dict[str, threading.Event] = {}


def _repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def _rollout_script() -> Path:
    env = os.environ.get("INA_ROLLOUT_SCRIPT")
    if env:
        return Path(env).resolve()
    return _repo_root() / "scripts" / "ina_profile_namespace_rollout.sh"


def _rollout_env(
    profile_name: str,
    req: ProfileRolloutRequest,
) -> dict:
    env = os.environ.copy()
    env["PROFILE_NS"] = profile_name
    env["REPO_ROOT"] = str(_repo_root())
    if req.slice_count is not None and req.slice_count > 0:
        env["SLICE_COUNT"] = str(req.slice_count)
    env["SKIP_UES"] = "1" if req.skip_ues else "0"
    env["SKIP_RAN"] = "1" if req.skip_ran else "0"
    env["ONLY_UES"] = "1" if req.only_ues else "0"
    env["IGNORE_PFCP"] = "1" if req.ignore_pfcp else "0"
    if req.ue_gap_sec is not None:
        env["UE_GAP_SEC"] = str(req.ue_gap_sec)
    if req.pdu_wait_sec is not None:
        env["PDU_WAIT_SEC"] = str(req.pdu_wait_sec)
    if req.du_settle_sec is not None:
        env["DU_SETTLE_SEC"] = str(req.du_settle_sec)
    if req.pfcp_wait_sec is not None:
        env["PFCP_WAIT_SEC"] = str(req.pfcp_wait_sec)
    if req.timeout_sec is not None:
        env["TIMEOUT"] = str(req.timeout_sec)
    return env


def _register(profile_name: str) -> threading.Event:
    """Register a cancel event for this profile; cancel any prior run."""
    ev = threading.Event()
    with _lock:
        old = _cancel_events.get(profile_name)
        if old is not None:
            old.set()
        _cancel_events[profile_name] = ev
    return ev


def _unregister(profile_name: str, ev: threading.Event) -> None:
    with _lock:
        if _cancel_events.get(profile_name) is ev:
            _cancel_events.pop(profile_name, None)


def stop_profile_rollout(profile_name: str) -> ProfileRolloutStopResponse:
    """Signal the active staged rollout for ``profile_name`` to stop."""
    with _lock:
        ev = _cancel_events.get(profile_name)
        if ev is None:
            return ProfileRolloutStopResponse(
                ok=True,
                profile=profile_name,
                stopped=False,
                message=f"No active rollout for profile {profile_name}",
            )
        ev.set()
    return ProfileRolloutStopResponse(
        ok=True,
        profile=profile_name,
        stopped=True,
        message=f"Stop signalled for profile {profile_name} rollout",
    )


def iter_profile_rollout_sse(
    profile_name: str,
    req: Optional[ProfileRolloutRequest] = None,
) -> Iterator[str]:
    """SSE stream for staged profile rollout script output."""
    req = req or ProfileRolloutRequest()
    script = _rollout_script()
    if not script.is_file():
        yield result_event(
            ProfileRolloutResponse(
                ok=False,
                profile=profile_name,
                message=f"Rollout script not found: {script}",
                exit_code=127,
            )
        )
        return

    wall = int(req.wall_timeout_sec or 900)
    cmd = ["bash", str(script)]
    cancel_event = _register(profile_name)
    yield status_event(
        f"Profile rollout for ns={profile_name} (wall {wall}s)",
        profile=profile_name,
    )
    yield status_event(f"$ {' '.join(cmd)}")

    cmd_result: CmdResult | None = None
    try:
        for item in stream_cmd(
            cmd,
            cwd=str(_repo_root()),
            env=_rollout_env(profile_name, req),
            timeout=float(wall),
            cancel_event=cancel_event,
        ):
            if isinstance(item, CmdResult):
                cmd_result = item
            else:
                stream, line = item
                yield log_event(stream, line)
    finally:
        _unregister(profile_name, cancel_event)

    assert cmd_result is not None
    if cmd_result.returncode == 124:
        yield result_event(
            ProfileRolloutResponse(
                ok=False,
                profile=profile_name,
                message=f"Rollout wall timeout after {wall}s",
                stdout=cmd_result.stdout,
                stderr=cmd_result.stderr,
                exit_code=124,
            )
        )
        return

    if cmd_result.returncode == 130 or cancel_event.is_set():
        yield result_event(
            ProfileRolloutResponse(
                ok=False,
                profile=profile_name,
                message="Rollout stopped",
                stdout=cmd_result.stdout,
                stderr=cmd_result.stderr,
                exit_code=130,
            )
        )
        return

    ok = cmd_result.returncode == 0
    yield result_event(
        ProfileRolloutResponse(
            ok=ok,
            profile=profile_name,
            message=(
                "Profile rollout completed"
                if ok
                else f"Profile rollout failed (exit {cmd_result.returncode})"
            ),
            stdout=cmd_result.stdout,
            stderr=cmd_result.stderr,
            exit_code=cmd_result.returncode,
        )
    )


def run_profile_rollout(
    profile_name: str,
    req: Optional[ProfileRolloutRequest] = None,
) -> ProfileRolloutResponse:
    """Blocking rollout (non-SSE); same cancel registry as the stream path."""
    req = req or ProfileRolloutRequest()
    out: ProfileRolloutResponse | None = None
    for chunk in iter_profile_rollout_sse(profile_name, req):
        # Last result event is the response; parse lightly from sse wrapper.
        if chunk.startswith("event: result\n"):
            import json

            data_line = ""
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    data_line += line[6:]
            if data_line:
                out = ProfileRolloutResponse.model_validate(json.loads(data_line))
    if out is None:
        return ProfileRolloutResponse(
            ok=False,
            profile=profile_name,
            message="Rollout produced no result",
            exit_code=1,
        )
    return out
