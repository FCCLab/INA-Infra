"""CPU-sweep step math + start/stop against a fake operator apply."""

from __future__ import annotations

import time

from app.schemas import (
    OperatorApplyReport,
    OperatorNfReported,
    OperatorRegisterRequest,
    BenchmarkRunRequest,
)
from app.services import benchmark_log, benchmark_run, benchmark_store, operators
from app.services.cmd_stream import log_event, result_event, status_event


def test_cpu_steps_increment_includes_max():
    assert benchmark_run.cpu_steps("100m", "500m", "100m") == [
        "100m",
        "200m",
        "300m",
        "400m",
        "500m",
    ]
    assert benchmark_run.cpu_steps("50m", "1000m", "20m") == [
        f"{m}m" for m in list(range(50, 991, 20)) + [1000]
    ]
    assert benchmark_run.cpu_steps("200m", "200m", "20m") == ["200m"]


def test_benchmark_log_tee_sse(tmp_path, monkeypatch):
    monkeypatch.setenv("INA_BENCHMARK_LOG", str(tmp_path / "tee.log"))

    def _events():
        yield status_event("rendering…")
        yield log_event("stdout", "wrote namespaces/oai-benchmark/upf.yaml")
        yield result_event({"ok": True, "message": "deployed"})

    out = list(benchmark_log.tee_sse("deploy", _events()))
    assert len(out) == 3
    text = (tmp_path / "tee.log").read_text(encoding="utf-8")
    assert "[deploy] rendering…" in text
    assert "wrote namespaces/oai-benchmark/upf.yaml" in text
    assert "result ok=True deployed" in text


def test_parse_format_cpu_millis():
    assert benchmark_run.parse_cpu_millis("500m") == 500
    assert benchmark_run.parse_cpu_millis("1") == 1000
    assert benchmark_run.format_cpu_millis(250) == "250m"
    assert benchmark_run.format_cpu_millis(1000) == "1000m"
    assert benchmark_run.format_cpu_millis(2000) == "2000m"


class _FakeLoop:
    def call_soon_threadsafe(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class _FakeQueue:
    def put_nowait(self, _item):
        return None


def _register(oid: str, nf: str = "oai-cu-up") -> None:
    operators.delete_operator(oid)
    operators.register(
        OperatorRegisterRequest(
            id=oid,
            cluster="edge",
            namespace="oai-benchmark",
            version="0.3.1",
            nfs=[
                OperatorNfReported(
                    name=nf,
                    kind="cuup",
                    namespace="oai-benchmark",
                    controllable=["cpu", "memory"],
                    cpu_limit="1000m",
                    cpu_request="1000m",
                    ready_replicas=1,
                    replicas=1,
                )
            ],
        )
    )
    operators.attach_ws(oid, _FakeLoop(), _FakeQueue())


def _auto_apply(orig_wait):
    def _wait(operator_id, nf, generation, *, timeout=90.0, cancel=None):
        operators.report_apply(
            operator_id,
            OperatorApplyReport(
                nf=nf,
                generation=generation,
                ok=True,
                cpu_limit="ok",
                cpu_request="ok",
                message="test apply",
            ),
        )
        return True, ""

    return _wait


def test_start_stop_records_step_times(monkeypatch, tmp_path):
    monkeypatch.setenv("INA_DB_PATH", str(tmp_path / "bench.db"))
    monkeypatch.setenv("INA_BENCHMARK_LOG", str(tmp_path / "benchmark.log"))
    # Re-bind store path after env (module already imported default).
    benchmark_store.init_db()
    oid = "edge-oai-benchmark"
    _register(oid)
    monkeypatch.setattr(benchmark_run, "wait_cpu_apply", _auto_apply(None))

    # Stop any leftover thread from prior tests.
    benchmark_run.stop_run()
    time.sleep(0.05)

    st = benchmark_run.start_run(
        BenchmarkRunRequest(
            min_cpu="100m",
            max_cpu="300m",
            cpu_step="100m",
            step_sec=0.2,
            warmup_sec=0.05,
            operator_id=oid,
            nf="oai-cu-up",
        )
    )
    assert st.running
    assert len(st.step_list) == 3
    assert [s.cpu for s in st.step_list] == ["100m", "200m", "300m"]

    deadline = time.time() + 8
    while time.time() < deadline:
        cur = benchmark_run.status()
        if cur.status == "done" and all(s.phase == "done" for s in cur.step_list):
            break
        time.sleep(0.05)
    cur = benchmark_run.status()
    assert cur.status == "done"
    assert len(cur.step_list) == 3
    for s in cur.step_list:
        assert s.phase == "done"
        assert s.started_at
        assert s.stopped_at
    log_text = (tmp_path / "benchmark.log").read_text(encoding="utf-8")
    assert "run " in log_text and " start " in log_text
    assert "100m" in log_text
    assert "complete" in log_text


def test_stop_during_run(monkeypatch, tmp_path):
    monkeypatch.setenv("INA_DB_PATH", str(tmp_path / "bench-stop.db"))
    monkeypatch.setenv("INA_BENCHMARK_LOG", str(tmp_path / "bench-stop.log"))
    benchmark_store.init_db()
    oid = "edge-oai-benchmark-stop"
    _register(oid)
    monkeypatch.setattr(benchmark_run, "wait_cpu_apply", _auto_apply(None))
    benchmark_run.stop_run()
    time.sleep(0.05)

    benchmark_run.start_run(
        BenchmarkRunRequest(
            min_cpu="100m",
            max_cpu="1000m",
            cpu_step="100m",
            step_sec=2.0,
            warmup_sec=2.0,
            operator_id=oid,
            nf="oai-cu-up",
        )
    )
    time.sleep(0.15)
    stop = benchmark_run.stop_run()
    assert stop.ok
    deadline = time.time() + 6
    while time.time() < deadline:
        cur = benchmark_run.status()
        if not cur.running:
            break
        time.sleep(0.05)
    cur = benchmark_run.status()
    assert not cur.running
    assert cur.status in ("stopped", "done", "error")
