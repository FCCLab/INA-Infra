"""SQLite persistence for INA-Infra profiles (identity + slices + network + PL result)."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

from app.schemas import NetworkIn, PlSolveResponse, Profile, ProfileRecord, SliceIn
from app.services import pl_solver


def _default_db_path() -> Path:
    env = os.environ.get("INA_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    repo = os.environ.get("REPO_ROOT")
    if repo:
        return Path(repo).resolve() / "ina-infra" / "data" / "profiles.db"
    return Path(__file__).resolve().parents[3] / "data" / "profiles.db"


def db_path() -> Path:
    return _default_db_path()


def _connect() -> sqlite3.Connection:
    path = db_path()
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


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "network_json" not in cols:
        conn.execute(
            "ALTER TABLE profiles ADD COLUMN network_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "pl_result_json" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN pl_result_json TEXT")
    if "pl_result_file" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN pl_result_file TEXT")


def init_db() -> Path:
    """Create schema, migrate, seed default profile if missing."""
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
              name TEXT PRIMARY KEY,
              subnet TEXT NOT NULL,
              max_slices INTEGER NOT NULL,
              dnn_prefix TEXT NOT NULL,
              slices_json TEXT NOT NULL,
              network_json TEXT NOT NULL DEFAULT '{}',
              pl_result_json TEXT,
              pl_result_file TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        _migrate(conn)
        # Backfill empty network_json with defaults
        default_net = json.dumps(
            pl_solver.default_network_in().model_dump(exclude_none=True)
        )
        conn.execute(
            "UPDATE profiles SET network_json = ? "
            "WHERE network_json IS NULL OR network_json = '' OR network_json = '{}'",
            (default_net,),
        )
        row = conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()
        if int(row["c"]) == 0:
            _upsert_conn(conn, _seed_record())
    return db_path()


def _seed_record() -> ProfileRecord:
    defs = pl_solver.profile_defaults()
    return ProfileRecord(
        profile=defs.profile,
        slices=defs.slices,
        network=defs.network,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _parse_network(raw: Any) -> NetworkIn:
    if raw is None or raw == "":
        return pl_solver.default_network_in()
    if isinstance(raw, str):
        data = json.loads(raw) if raw.strip() else {}
    else:
        data = raw
    if not data:
        return pl_solver.default_network_in()
    return NetworkIn.model_validate(data)


def _parse_pl_result(raw: Any) -> Optional[PlSolveResponse]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        data = json.loads(raw)
    else:
        data = raw
    if not data:
        return None
    return PlSolveResponse.model_validate(data)


def _row_to_record(row: sqlite3.Row) -> ProfileRecord:
    keys = row.keys()
    slices = [SliceIn.model_validate(s) for s in json.loads(row["slices_json"])]
    network = _parse_network(row["network_json"] if "network_json" in keys else "{}")
    pl_result = _parse_pl_result(
        row["pl_result_json"] if "pl_result_json" in keys else None
    )
    pl_result_file = None
    if "pl_result_file" in keys and row["pl_result_file"]:
        pl_result_file = str(row["pl_result_file"])
    profile = Profile(
        name=row["name"],
        subnet=row["subnet"],
        max_slices=int(row["max_slices"]),
        dnn_prefix=row["dnn_prefix"],
    )
    return ProfileRecord(
        profile=profile,
        slices=slices,
        network=network,
        pl_result=pl_result,
        pl_result_file=pl_result_file,
        updated_at=row["updated_at"],
    )


def _upsert_conn(conn: sqlite3.Connection, rec: ProfileRecord) -> None:
    Profile.model_validate(rec.profile.model_dump())
    now = datetime.now(timezone.utc).isoformat()
    network = rec.network or pl_solver.default_network_in()

    # Preserve prior PL result when caller omits it (e.g. SLA-only Update).
    existing = conn.execute(
        "SELECT pl_result_json, pl_result_file FROM profiles WHERE name = ?",
        (rec.profile.name,),
    ).fetchone()
    if rec.pl_result is not None:
        pl_json = json.dumps(rec.pl_result.model_dump(exclude_none=True))
        pl_file = rec.pl_result_file
    elif existing is not None:
        pl_json = existing["pl_result_json"]
        pl_file = (
            rec.pl_result_file
            if rec.pl_result_file is not None
            else existing["pl_result_file"]
        )
    else:
        pl_json = None
        pl_file = rec.pl_result_file

    conn.execute(
        """
        INSERT INTO profiles (
          name, subnet, max_slices, dnn_prefix, slices_json, network_json,
          pl_result_json, pl_result_file, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
          subnet=excluded.subnet,
          max_slices=excluded.max_slices,
          dnn_prefix=excluded.dnn_prefix,
          slices_json=excluded.slices_json,
          network_json=excluded.network_json,
          pl_result_json=excluded.pl_result_json,
          pl_result_file=excluded.pl_result_file,
          updated_at=excluded.updated_at
        """,
        (
            rec.profile.name,
            rec.profile.subnet,
            rec.profile.max_slices,
            rec.profile.dnn_prefix,
            json.dumps([s.model_dump() for s in rec.slices]),
            json.dumps(network.model_dump(exclude_none=True)),
            pl_json,
            pl_file,
            now,
        ),
    )


def list_profiles() -> List[ProfileRecord]:
    init_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM profiles ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_names() -> List[str]:
    return [r.profile.name for r in list_profiles()]


def get_profile(name: str) -> Optional[ProfileRecord]:
    init_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def save_profile(rec: ProfileRecord) -> ProfileRecord:
    """Create or update profile by name (slices + network + optional PL result)."""
    init_db()
    with _db() as conn:
        _upsert_conn(conn, rec)
        row = conn.execute(
            "SELECT * FROM profiles WHERE name = ?", (rec.profile.name,)
        ).fetchone()
    assert row is not None
    return _row_to_record(row)


def save_pl_result(
    name: str,
    result: PlSolveResponse,
    *,
    result_file: Optional[str] = None,
    slices: Optional[List[SliceIn]] = None,
    network: Optional[NetworkIn] = None,
) -> Optional[ProfileRecord]:
    """Attach last PL result to an existing profile (no-op if profile missing)."""
    rec = get_profile(name)
    if rec is None:
        return None
    updates: dict[str, Any] = {
        "pl_result": result,
        "pl_result_file": result_file,
    }
    if slices is not None:
        updates["slices"] = slices
    if network is not None:
        updates["network"] = network
    return save_profile(rec.model_copy(update=updates))


def create_profile(rec: ProfileRecord) -> ProfileRecord:
    init_db()
    if get_profile(rec.profile.name) is not None:
        raise ValueError(f"profile already exists: {rec.profile.name}")
    return save_profile(rec)


def delete_profile(name: str) -> bool:
    init_db()
    with _db() as conn:
        cur = conn.execute("DELETE FROM profiles WHERE name = ?", (name,))
        deleted = cur.rowcount > 0
        count = int(conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()["c"])
        if count == 0:
            _upsert_conn(conn, _seed_record())
    return deleted
