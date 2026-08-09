from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest


SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[1]
CLEAR_CONFIRMATION = "CLEAR_XIAOXIN_TEST_DATA"

IDENTITY_CLEAR_ORDER = (
    "admin_audit_log",
    "student_todos",
    "student_courses",
    "student_course_reminder_settings",
    "student_semesters",
    "student_profiles",
    "subject_aliases",
    "memory_subjects",
    "speaker_profiles",
    "personal_pets",
    "sessions",
    "devices",
    "users",
)

COMPANION_CLEAR_ORDER = (
    "companion_context_job_pins",
    "semantic_memory_evaluations",
    "companion_context_messages",
    "companion_turn_sources",
    "pending_companion_observations",
    "observation_evidence",
    "companion_observations",
    "companion_retrieval_audits",
    "relationship_stage_events",
    "companion_va_events",
    "companion_va_snapshots",
    "companion_interaction_contracts",
    "evidence_relations",
    "capsule_evidence",
    "session_capsules",
    "adjustment_evidence_qualification",
    "adjustment_evidence",
    "companion_adjustments",
    "chapter_evidence",
    "companion_chapter_boundaries",
    "companion_chapters",
    "companion_growth_moment_evidence",
    "companion_growth_moment_boundaries",
    "companion_growth_moment_metadata",
    "companion_growth_moments",
    "companion_narrative_boundaries",
    "companion_narrative_preferences",
    "companion_academic_transitions",
    "companion_academic_states",
    "companion_birth_temperaments",
    "memory_controls",
    "consolidation_jobs",
    "initiative_decisions",
    "initiative_opportunities",
    "companion_turns",
    "companion_evidence",
    "relationship_epochs",
    "companion_pets",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path | None, value: dict[str, Any]) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_command(command: list[str], cwd: Path, timeout: float = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": result.returncode == 0,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def git_summary(repo_root: Path) -> dict[str, Any]:
    branch = run_command(["git", "branch", "--show-current"], repo_root)
    head = run_command(["git", "rev-parse", "HEAD"], repo_root)
    origin_main = run_command(["git", "rev-parse", "--verify", "origin/main"], repo_root)
    status = run_command(["git", "status", "--porcelain"], repo_root)
    dirty = [
        line
        for line in str(status.get("stdout", "")).splitlines()
        if line and ".playwright-cli/" not in line
    ]
    return {
        "branch": branch,
        "head": head,
        "origin_main": origin_main,
        "dirty_paths_excluding_playwright_cli": dirty,
        "is_clean_excluding_playwright_cli": not dirty,
    }


def selected_llm_summary(config_path: Path) -> dict[str, Any]:
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    selected: dict[str, str] = {}
    ali_model = ""
    in_selected = False
    in_ali = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "selected_module:":
            in_selected = True
            in_ali = False
            continue
        if stripped == "AliLLM:":
            in_selected = False
            in_ali = True
            continue
        if line and not line.startswith((" ", "\t")) and stripped.endswith(":"):
            in_selected = False
            in_ali = False
        if in_selected and ":" in stripped and not stripped.startswith("#"):
            key, value = stripped.split(":", 1)
            selected[key.strip()] = value.strip().strip('"').strip("'")
        if in_ali and stripped.startswith("model_name:"):
            ali_model = stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return {
        "config_path": str(config_path),
        "selected_module": selected,
        "ali_llm_model_name": ali_model,
    }


def sqlite_table_counts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "tables": {}}
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        counts: dict[str, int | str] = {}
        for (name,) in rows:
            try:
                counts[str(name)] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            except sqlite3.DatabaseError as exc:
                counts[str(name)] = f"unavailable: {exc}"
    return {"path": str(path), "exists": True, "tables": counts}


def health_probe(url: str, timeout: float) -> dict[str, Any]:
    if not url:
        return {"skipped": True}
    try:
        with urlrequest.urlopen(url, timeout=timeout) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            return {"ok": 200 <= response.status < 400, "status": response.status, "body_prefix": body}
    except (OSError, urlerror.URLError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def docker_compose_summary(server_root: Path, compose_file: Path | None) -> dict[str, Any]:
    compose_file = compose_file or server_root / "docker-compose.yml"
    if not compose_file.exists():
        return {"skipped": True, "reason": "compose file missing", "compose_file": str(compose_file)}
    return run_command(["docker", "compose", "-f", str(compose_file), "ps"], server_root, timeout=20)


def release_blockers(
    repo_root: Path, git: dict[str, Any], identity_db: Path, companion_db: Path
) -> list[str]:
    blockers: list[str] = []
    if not git.get("is_clean_excluding_playwright_cli"):
        blockers.append("local worktree has uncommitted tracked changes")
    head = str(git.get("head", {}).get("stdout", "")).strip()
    origin_main = str(git.get("origin_main", {}).get("stdout", "")).strip()
    if head and origin_main and head != origin_main:
        blockers.append("local HEAD differs from local origin/main; fetch real remote before release")
    if not identity_db.exists():
        blockers.append("identity database is missing")
    if not companion_db.exists():
        blockers.append("companion database is missing")
    if not (repo_root / "main" / "xiaozhi-server" / "docker-compose.yml").exists():
        blockers.append("server docker-compose.yml is missing")
    return blockers


def build_preflight_report(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = args.repo_root.resolve()
    server_root = args.server_root.resolve()
    identity_db = args.identity_db.resolve()
    companion_db = args.companion_db.resolve()
    git = git_summary(repo_root)
    return {
        "generated_at": now_iso(),
        "mode": "preflight",
        "repo_root": str(repo_root),
        "server_root": str(server_root),
        "git": git,
        "llm": selected_llm_summary(args.config.resolve()),
        "identity_db": sqlite_table_counts(identity_db),
        "companion_db": sqlite_table_counts(companion_db),
        "docker_compose": docker_compose_summary(
            server_root, args.compose_file.resolve() if args.compose_file else None
        ),
        "health": health_probe(args.health_url, args.health_timeout),
        "release_blockers": release_blockers(repo_root, git, identity_db, companion_db),
    }


def existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def backup_sqlite(source: Path, backup_dir: Path) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{source.stem}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.db"
    shutil.copy2(source, destination)
    return {
        "source": str(source),
        "backup": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def clear_sqlite(
    label: str,
    path: Path,
    clear_order: tuple[str, ...],
    *,
    execute: bool,
    backup_dir: Path | None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "exists": path.exists(),
        "execute": execute,
        "backup": None,
        "tables": [],
    }
    if not path.exists():
        return report
    if execute and backup_dir is not None:
        report["backup"] = backup_sqlite(path, backup_dir)
    before = sqlite_table_counts(path)["tables"]
    with sqlite3.connect(path) as conn:
        present = existing_tables(conn)
        touched: list[dict[str, Any]] = []
        if execute:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN")
        try:
            for table in clear_order:
                if table not in present:
                    continue
                if execute:
                    conn.execute(f'DELETE FROM "{table}"')
                touched.append(
                    {
                        "table": table,
                        "before": before.get(table, 0),
                        "action": "delete" if execute else "would_delete",
                    }
                )
            if execute:
                conn.commit()
                conn.execute("VACUUM")
        except Exception:
            if execute:
                conn.rollback()
            raise
        finally:
            if execute:
                conn.execute("PRAGMA foreign_keys=ON")
    after = sqlite_table_counts(path)["tables"] if execute else before
    report["tables"] = [{**item, "after": after.get(item["table"], item["before"])} for item in touched]
    return report


def build_clear_report(args: argparse.Namespace) -> dict[str, Any]:
    execute = bool(args.execute)
    if execute and args.confirm != CLEAR_CONFIRMATION:
        raise SystemExit(f"--confirm must exactly equal {CLEAR_CONFIRMATION!r} when --execute is used")
    backup_dir = args.backup_dir.resolve() if args.backup_dir else None
    return {
        "generated_at": now_iso(),
        "mode": "clear-test-data",
        "execute": execute,
        "confirmation_required_for_execute": CLEAR_CONFIRMATION,
        "targets": [
            clear_sqlite(
                "identity",
                args.identity_db.resolve(),
                IDENTITY_CLEAR_ORDER,
                execute=execute,
                backup_dir=backup_dir,
            ),
            clear_sqlite(
                "companion",
                args.companion_db.resolve(),
                COMPANION_CLEAR_ORDER,
                execute=execute,
                backup_dir=backup_dir,
            ),
        ],
    }


def longrun_plan(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "mode": "dual-device-longrun-plan",
        "duration_hours": args.duration_hours,
        "base_url": args.base_url,
        "text_chat_endpoint_template": "/api/xiaoxin/devices/{device_id}/text-chat",
        "devices": [
            {
                "slot": "A",
                "device_id": args.device_a_id,
                "speaker_profile_id": args.device_a_speaker_profile_id,
                "memory_subject_id": args.device_a_memory_subject_id,
                "pet_id": args.device_a_pet_id,
            },
            {
                "slot": "B",
                "device_id": args.device_b_id,
                "speaker_profile_id": args.device_b_speaker_profile_id,
                "memory_subject_id": args.device_b_memory_subject_id,
                "pet_id": args.device_b_pet_id,
            },
        ],
        "scenarios": [
            "boot_and_idle_baseline",
            "wake_then_text_chat",
            "four_to_six_turn_learning_pressure",
            "four_to_six_turn_low_mood_no_interrogation",
            "preference_change_and_correction",
            "device_offline_then_wake_recovery",
            "server_restart_reconnect",
            "tts_failure_or_busy_device_observation",
            "same_scene_ab_personality_difference",
            "final_no_cross_subject_recall",
        ],
        "per_round_required_fields": [
            "device_id",
            "speaker_profile_id",
            "memory_subject_id",
            "pet_id",
            "submitted_at",
            "input_text",
            "server_reply",
            "tts_state_sequence",
            "serial_state",
            "human_hearing_note",
            "visual_note",
        ],
        "hard_failures": [
            "cross_subject_private_memory_recall",
            "wrong_speaker_profile_used",
            "candidate_or_old_epoch_memory_used_as_fact",
            "repeated_tts_delivery_after_reconnect",
            "device_rebind_causes_cross_user_delivery",
        ],
    }


def command_preflight(args: argparse.Namespace) -> int:
    write_json(args.output, build_preflight_report(args))
    return 0


def command_clear_test_data(args: argparse.Namespace) -> int:
    write_json(args.output, build_clear_report(args))
    return 0


def command_longrun_plan(args: argparse.Namespace) -> int:
    write_json(args.output, longrun_plan(args))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Xiaoxin release readiness, test-data cleanup, and dual-device long-run planning."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="collect a read-only release readiness report")
    preflight.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    preflight.add_argument("--server-root", type=Path, default=SERVER_ROOT)
    preflight.add_argument("--config", type=Path, default=SERVER_ROOT / "config.yaml")
    preflight.add_argument("--identity-db", type=Path, default=SERVER_ROOT / "data" / "xiaoxin_control.db")
    preflight.add_argument("--companion-db", type=Path, default=SERVER_ROOT / "data" / "xiaoxin_companion.db")
    preflight.add_argument("--compose-file", type=Path)
    preflight.add_argument("--health-url", default="")
    preflight.add_argument("--health-timeout", type=float, default=5)
    preflight.add_argument("--output", type=Path)
    preflight.set_defaults(handler=command_preflight)

    clear = sub.add_parser("clear-test-data", help="dry-run or execute Xiaoxin test-data cleanup")
    clear.add_argument("--identity-db", type=Path, default=SERVER_ROOT / "data" / "xiaoxin_control.db")
    clear.add_argument("--companion-db", type=Path, default=SERVER_ROOT / "data" / "xiaoxin_companion.db")
    clear.add_argument("--backup-dir", type=Path)
    clear.add_argument("--execute", action="store_true")
    clear.add_argument("--confirm", default="")
    clear.add_argument("--output", type=Path)
    clear.set_defaults(handler=command_clear_test_data)

    longrun = sub.add_parser("longrun-plan", help="write the dual-device 24h long-run evidence plan")
    longrun.add_argument("--base-url", default="http://127.0.0.1:8003")
    longrun.add_argument("--duration-hours", type=int, default=24)
    longrun.add_argument("--device-a-id", default="")
    longrun.add_argument("--device-a-speaker-profile-id", default="")
    longrun.add_argument("--device-a-memory-subject-id", default="")
    longrun.add_argument("--device-a-pet-id", default="")
    longrun.add_argument("--device-b-id", default="")
    longrun.add_argument("--device-b-speaker-profile-id", default="")
    longrun.add_argument("--device-b-memory-subject-id", default="")
    longrun.add_argument("--device-b-pet-id", default="")
    longrun.add_argument("--output", type=Path)
    longrun.set_defaults(handler=command_longrun_plan)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
