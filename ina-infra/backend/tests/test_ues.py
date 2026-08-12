"""UE iperf WebSocket registry unit tests."""

from __future__ import annotations

from app.schemas import UeDeclare, UeDesiredRequest
from app.services import ues


def test_per_ue_desired():
    a = ues.register(
        UeDeclare(
            id="ue-a",
            cluster="edge",
            namespace="oai-benchmark",
            pod="pod-a",
            protocol="udp",
            status="running",
            server="10.1.139.35",
            port=5210,
        )
    )
    b = ues.register(
        UeDeclare(
            id="ue-b",
            cluster="edge",
            namespace="oai-benchmark",
            pod="pod-b",
            protocol="udp",
            status="running",
            server="10.1.139.35",
            port=5210,
        )
    )
    assert a.id == "ue-a" and b.id == "ue-b"

    da = ues.set_desired(
        UeDesiredRequest(id="ue-a", protocol="tcp", action="start", parallel=3)
    )
    db = ues.set_desired(
        UeDesiredRequest(
            id="ue-b", protocol="udp", action="start", bandwidth="80M", parallel=8
        )
    )
    assert da.protocol == "tcp" and da.parallel == 3
    assert db.protocol == "udp" and db.bandwidth == "80M" and db.parallel == 8

    listed = {u.id: u for u in ues.list_ues().ues}
    assert listed["ue-a"].desired is not None
    assert listed["ue-a"].desired.protocol == "tcp"
    assert listed["ue-b"].desired is not None
    assert listed["ue-b"].desired.parallel == 8

    ues.delete_ue("ue-a")
    ues.delete_ue("ue-b")


def test_set_desired_requires_id():
    try:
        ues.set_desired(UeDesiredRequest(id="", protocol="tcp"))  # type: ignore[arg-type]
        assert False, "expected validation or value error"
    except Exception:
        pass


def test_register_and_list():
    body = UeDeclare(
        id="test-ue-1",
        cluster="edge",
        namespace="oai-benchmark",
        pod="oai-ue-xyz",
        protocol="udp",
        status="running",
        server="10.1.139.35",
        port=5210,
        mbits_per_second=12.5,
    )
    out = ues.register(body)
    assert out.id == "test-ue-1"
    assert out.desired is not None
    assert out.desired.protocol == "udp"
    listed = ues.list_ues()
    ids = {u.id for u in listed.ues}
    assert "test-ue-1" in ids
    ues.delete_ue("test-ue-1")


def test_desired_survives_prune_and_reconnect():
    ues.register(
        UeDeclare(
            id="ue-keep",
            protocol="udp",
            status="running",
            server="10.1.139.35",
            reverse=False,
            bandwidth="25M",
            parallel=5,
        )
    )
    d = ues.set_desired(
        UeDesiredRequest(
            id="ue-keep",
            protocol="udp",
            action="start",
            reverse=False,
            bandwidth="25M",
            parallel=5,
        )
    )
    assert d.generation == 1 and d.reverse is False
    with ues._lock:
        cur = ues._ues["ue-keep"]
        cur["ws_connected"] = False
        cur["last_seen"] = "2000-01-01T00:00:00+00:00"
        ues._prune_stale_locked()
    assert "ue-keep" not in ues._ues
    # Reconnect declare reports DL defaults — cached UL desired must win.
    out = ues.register(
        UeDeclare(
            id="ue-keep",
            protocol="udp",
            status="running",
            server="10.1.139.35",
            reverse=True,
            bandwidth="50M",
            parallel=5,
        )
    )
    assert out.desired is not None
    assert out.desired.generation == 1
    assert out.desired.reverse is False
    assert out.desired.bandwidth == "25M"
    ues.delete_ue("ue-keep")
