"""SQLite persistence for oai-benchmark CPU-sweep runs + steps.

Throughput is stored on the step row for later analysis; the HTTP status API
exposes start/stop times only.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

from app.schemas import BenchmarkRunStatusOut, BenchmarkStepOut


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    from app.services import paths as ina_paths

    return ina_paths.default_db_path()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> Path:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              status TEXT NOT NULL,
              message TEXT NOT NULL DEFAULT '',
              operator_id TEXT NOT NULL,
              nf TEXT NOT NULL,
              min_cpu TEXT NOT NULL,
              max_cpu TEXT NOT NULL,
              steps INTEGER NOT NULL,
              step_sec REAL NOT NULL,
              warmup_sec REAL NOT NULL,
              current_index INTEGER,
              started_at TEXT,
              finished_at TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_steps (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id INTEGER NOT NULL,
              step_index INTEGER NOT NULL,
              cpu TEXT NOT NULL,
              phase TEXT NOT NULL DEFAULT 'pending',
              started_at TEXT,
              stopped_at TEXT,
              message TEXT NOT NULL DEFAULT '',
              throughput_mbps REAL,
              FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE,
              UNIQUE (run_id, step_index)
            )
            """
        )
    return _db_path()


def create_run(
    *,
    operator_id: str,
    nf: str,
    min_cpu: str,
    max_cpu: str,
    steps: int,
    step_sec: float,
    warmup_sec: float,
    cpus: List[str],
) -> int:
    now = _now()
    with _db() as conn:
        cur = conn.execute(
            """
            INSERT INTO benchmark_runs (
              status, message, operator_id, nf, min_cpu, max_cpu, steps,
              step_sec, warmup_sec, current_index, started_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "running",
                "",
                operator_id,
                nf,
                min_cpu,
                max_cpu,
                steps,
                step_sec,
                warmup_sec,
                None,
                now,
                now,
            ),
        )
        run_id = int(cur.lastrowid)
        for i, cpu in enumerate(cpus):
            conn.execute(
                """
                INSERT INTO benchmark_steps (run_id, step_index, cpu, phase)
                VALUES (?, ?, ?, 'pending')
                """,
                (run_id, i, cpu),
            )
    return run_id


def update_run(
    run_id: int,
    *,
    status: Optional[str] = None,
    message: Optional[str] = None,
    current_index: Optional[int] = None,
    finished: bool = False,
) -> None:
    sets: List[str] = []
    args: List[Any] = []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if message is not None:
        sets.append("message = ?")
        args.append(message)
    if current_index is not None:
        sets.append("current_index = ?")
        args.append(current_index)
    if finished:
        sets.append("finished_at = ?")
        args.append(_now())
    if not sets:
        return
    args.append(run_id)
    with _db() as conn:
        conn.execute(
            f"UPDATE benchmark_runs SET {', '.join(sets)} WHERE id = ?",
            args,
        )


def update_step(
    run_id: int,
    index: int,
    *,
    phase: Optional[str] = None,
    started_at: Optional[str] = None,
    stopped_at: Optional[str] = None,
    message: Optional[str] = None,
    throughput_mbps: Optional[float] = None,
    set_started: bool = False,
    set_stopped: bool = False,
) -> None:
    sets: List[str] = []
    args: List[Any] = []
    if phase is not None:
        sets.append("phase = ?")
        args.append(phase)
    if set_started:
        sets.append("started_at = ?")
        args.append(started_at or _now())
    elif started_at is not None:
        sets.append("started_at = ?")
        args.append(started_at)
    if set_stopped:
        sets.append("stopped_at = ?")
        args.append(stopped_at or _now())
    elif stopped_at is not None:
        sets.append("stopped_at = ?")
        args.append(stopped_at)
    if message is not None:
        sets.append("message = ?")
        args.append(message)
    if throughput_mbps is not None:
        sets.append("throughput_mbps = ?")
        args.append(throughput_mbps)
    if not sets:
        return
    args.extend([run_id, index])
    with _db() as conn:
        conn.execute(
            f"UPDATE benchmark_steps SET {', '.join(sets)} "
            "WHERE run_id = ? AND step_index = ?",
            args,
        )


def _row_to_status(run: sqlite3.Row, steps: List[sqlite3.Row]) -> BenchmarkRunStatusOut:
    return BenchmarkRunStatusOut(
        id=int(run["id"]),
        running=str(run["status"]) == "running",
        status=str(run["status"]),
        message=str(run["message"] or ""),
        operator_id=str(run["operator_id"]),
        nf=str(run["nf"]),
        min_cpu=str(run["min_cpu"]),
        max_cpu=str(run["max_cpu"]),
        steps=int(run["steps"]),
        step_sec=float(run["step_sec"]),
        warmup_sec=float(run["warmup_sec"]),
        current_index=(
            int(run["current_index"]) if run["current_index"] is not None else None
        ),
        started_at=run["started_at"],
        finished_at=run["finished_at"],
        step_list=[
            BenchmarkStepOut(
                index=int(s["step_index"]),
                cpu=str(s["cpu"]),
                phase=str(s["phase"]),
                started_at=s["started_at"],
                stopped_at=s["stopped_at"],
                message=str(s["message"] or ""),
            )
            for s in steps
        ],
    )


def get_run(run_id: int) -> Optional[BenchmarkRunStatusOut]:
    with _db() as conn:
        run = conn.execute(
            "SELECT * FROM benchmark_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not run:
            return None
        steps = conn.execute(
            "SELECT * FROM benchmark_steps WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        ).fetchall()
    return _row_to_status(run, steps)


def latest_run() -> Optional[BenchmarkRunStatusOut]:
    with _db() as conn:
        run = conn.execute(
            "SELECT * FROM benchmark_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not run:
            return None
        steps = conn.execute(
            "SELECT * FROM benchmark_steps WHERE run_id = ? ORDER BY step_index",
            (int(run["id"]),),
        ).fetchall()
    return _row_to_status(run, steps)
