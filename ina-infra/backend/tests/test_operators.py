"""Tests for connected RAN operator agent registry / compute resource targets."""

from __future__ import annotations

from app.schemas import (
    OperatorApplyReport,
    OperatorNfReported,
    OperatorRegisterRequest,
    OperatorResourceSetRequest,
)
from app.services import operators


def test_register_set_resources_desired_and_apply_report():
    oid = "test-edge-oai-benchmark"
    operators.delete_operator(oid)

    out = operators.register(
        OperatorRegisterRequest(
            id=oid,
            cluster="edge",
            namespace="oai-benchmark",
            version="0.3.0",
            nfs=[
                OperatorNfReported(
                    name="oai-cu-up",
                    kind="cuup",
                    namespace="oai-benchmark",
                    controllable=["cpu", "memory"],
                    cpu_limit="200m",
                    cpu_request="50m",
                    memory_limit="256Mi",
                    memory_request="128Mi",
                    ready_replicas=1,
                    replicas=1,
                )
            ],
        )
    )
    assert out.id == oid
    assert out.online
    assert len(out.nfs) == 1
    assert out.nfs[0].reported_cpu_limit == "200m"
    assert out.nfs[0].reported_memory_limit == "256Mi"
    assert out.nfs[0].controllable == ["cpu", "memory"]

    out = operators.set_resources(
        oid,
        "oai-cu-up",
        OperatorResourceSetRequest(
            cpu_limit="300m",
            cpu_request="50m",
            memory_limit="512Mi",
        ),
    )
    assert out.nfs[0].desired is not None
    assert out.nfs[0].desired.cpu_limit == "300m"
    assert out.nfs[0].desired.memory_limit == "512Mi"
    assert out.nfs[0].desired.generation == 1
    assert out.nfs[0].apply_status == "pending"

    desired = operators.desired(oid)
    assert "oai-cu-up" in desired.targets
    assert desired.targets["oai-cu-up"].cpu_limit == "300m"

    out = operators.report_apply(
        oid,
        OperatorApplyReport(
            nf="oai-cu-up",
            generation=1,
            ok=True,
            cpu_limit="300m",
            cpu_request="50m",
            memory_limit="512Mi",
            message="patched",
        ),
    )
    assert out.nfs[0].apply_status == "ok"
    assert out.nfs[0].applied_generation == 1

    listed = operators.list_operators()
    assert any(c.id == oid for c in listed.operators)

    assert operators.delete_operator(oid)


def test_register_seeds_desired_from_reported_on_connect():
    oid = "test-seed-desired"
    operators.delete_operator(oid)

    # Stale desired from a previous UI session.
    operators.register(
        OperatorRegisterRequest(
            id=oid,
            cluster="edge",
            namespace="oai-benchmark",
            nfs=[
                OperatorNfReported(
                    name="oai-cu-up",
                    kind="cuup",
                    cpu_limit="1000m",
                    cpu_request="1000m",
                )
            ],
        )
    )
    operators.set_resources(
        oid,
        "oai-cu-up",
        OperatorResourceSetRequest(cpu_limit="10m", cpu_request="10m"),
    )
    assert operators.desired(oid).targets["oai-cu-up"].cpu_limit == "10m"
    assert operators.desired(oid).targets["oai-cu-up"].generation == 1

    # Connect/declare with live values seeds desired (gen 0) from reported.
    out = operators.register(
        OperatorRegisterRequest(
            id=oid,
            cluster="edge",
            namespace="oai-benchmark",
            nfs=[
                OperatorNfReported(
                    name="oai-cu-up",
                    kind="cuup",
                    cpu_limit="200m",
                    cpu_request="50m",
                )
            ],
        ),
        seed_desired_from_reported=True,
    )
    assert out.nfs[0].reported_cpu_limit == "200m"
    assert out.nfs[0].desired is not None
    assert out.nfs[0].desired.cpu_limit == "200m"
    assert out.nfs[0].desired.cpu_request == "50m"
    assert out.nfs[0].desired.generation == 0
    assert out.nfs[0].apply_status == ""

    # Periodic declare must not wipe a newer UI desired.
    operators.set_resources(
        oid,
        "oai-cu-up",
        OperatorResourceSetRequest(cpu_limit="300m"),
    )
    operators.register(
        OperatorRegisterRequest(
            id=oid,
            cluster="edge",
            namespace="oai-benchmark",
            nfs=[
                OperatorNfReported(
                    name="oai-cu-up",
                    kind="cuup",
                    cpu_limit="200m",
                    cpu_request="50m",
                )
            ],
        ),
        seed_desired_from_reported=False,
    )
    assert operators.desired(oid).targets["oai-cu-up"].cpu_limit == "300m"
    assert operators.desired(oid).targets["oai-cu-up"].generation == 1

    assert operators.delete_operator(oid)
