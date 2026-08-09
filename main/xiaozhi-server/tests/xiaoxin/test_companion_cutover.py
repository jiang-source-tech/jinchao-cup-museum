from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVER_ROOT.parents[1]


def _source(relative_path):
    return (SERVER_ROOT / relative_path).read_text(encoding="utf-8")


def test_production_uses_only_companion_mind_and_has_no_legacy_memory_package():
    legacy_package = SERVER_ROOT / "core" / "xiaoxin" / "memory"
    assert not legacy_package.exists() or not tuple(legacy_package.glob("*.py"))

    runtime_source = _source("core/xiaoxin/runtime.py")
    connection_source = _source("core/connection.py")
    handler_source = _source("core/api/xiaoxin_control_handler.py")
    types_source = _source("core/xiaoxin/types.py")
    config_source = _source("config.yaml")
    local_config_source = _source("data/.config.yaml")

    combined_runtime = runtime_source + connection_source + handler_source
    assert "core.xiaoxin.memory" not in combined_runtime
    assert "from .memory" not in combined_runtime
    assert "MemoryOrchestrator" not in runtime_source
    assert "MemoryEngine" not in runtime_source
    assert "relationship_state" not in runtime_source
    assert "trusted_memory" not in runtime_source
    assert "commit_owner" not in runtime_source
    assert "legacy-memory" not in handler_source
    assert "memory_dir" not in types_source
    assert "companion_memory_v2_enabled" not in types_source
    assert "memory_dir:" not in config_source
    assert "memory_dir:" not in local_config_source


def test_default_companion_database_path_is_the_only_memory_path():
    types_source = _source("core/xiaoxin/types.py")
    config_source = _source("config.yaml")
    local_config_source = _source("data/.config.yaml")

    assert 'Path("data/xiaoxin_companion.db")' in types_source
    assert "companion_db_path: data/xiaoxin_companion.db" in config_source
    assert "companion_db_path: data/xiaoxin_companion.db" in local_config_source


def test_cutover_documents_read_only_legacy_backup_and_one_way_rollback():
    backup_doc = (
        REPO_ROOT / "docs" / "operations" / "backup-and-upgrade.md"
    ).read_text(encoding="utf-8")

    assert "data/xiaoxin_memory/" in backup_doc
    assert "xiaoxin_memory.db" in backup_doc
    assert "data/xiaoxin_companion.db" in backup_doc
    assert "只读归档" in backup_doc
    assert "不在运行时导入" in backup_doc
    assert "禁止把 V2 Evidence 反向写回旧 JSON" in backup_doc
