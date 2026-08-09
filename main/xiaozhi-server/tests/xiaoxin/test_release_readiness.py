from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sqlite3

import pytest


SERVER_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = SERVER_ROOT / "scripts" / "xiaoxin_release_readiness.py"


def load_release_readiness_module():
    spec = importlib.util.spec_from_file_location("xiaoxin_release_readiness", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_db(path: Path, table: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        conn.execute(f"INSERT INTO {table} (id) VALUES ('row-1')")


def row_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def clear_args(identity_db: Path, companion_db: Path, **overrides):
    values = {
        "identity_db": identity_db,
        "companion_db": companion_db,
        "backup_dir": None,
        "execute": False,
        "confirm": "",
        "output": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_clear_test_data_dry_run_reports_without_deleting(tmp_path: Path):
    module = load_release_readiness_module()
    identity_db = tmp_path / "identity.db"
    companion_db = tmp_path / "companion.db"
    create_db(identity_db, "users")
    create_db(companion_db, "companion_evidence")

    report = module.build_clear_report(clear_args(identity_db, companion_db))

    assert report["execute"] is False
    assert row_count(identity_db, "users") == 1
    assert row_count(companion_db, "companion_evidence") == 1
    identity_tables = report["targets"][0]["tables"]
    companion_tables = report["targets"][1]["tables"]
    assert identity_tables[0]["action"] == "would_delete"
    assert companion_tables[0]["action"] == "would_delete"


def test_clear_test_data_execute_requires_exact_confirmation(tmp_path: Path):
    module = load_release_readiness_module()
    identity_db = tmp_path / "identity.db"
    companion_db = tmp_path / "companion.db"
    create_db(identity_db, "users")
    create_db(companion_db, "companion_evidence")

    with pytest.raises(SystemExit):
        module.build_clear_report(
            clear_args(identity_db, companion_db, execute=True, confirm="CLEAR")
        )


def test_clear_test_data_execute_deletes_known_tables(tmp_path: Path):
    module = load_release_readiness_module()
    identity_db = tmp_path / "identity.db"
    companion_db = tmp_path / "companion.db"
    create_db(identity_db, "users")
    create_db(companion_db, "companion_evidence")

    report = module.build_clear_report(
        clear_args(
            identity_db,
            companion_db,
            execute=True,
            confirm=module.CLEAR_CONFIRMATION,
        )
    )

    assert report["execute"] is True
    assert row_count(identity_db, "users") == 0
    assert row_count(companion_db, "companion_evidence") == 0
    assert report["targets"][0]["tables"][0]["after"] == 0
    assert report["targets"][1]["tables"][0]["after"] == 0
