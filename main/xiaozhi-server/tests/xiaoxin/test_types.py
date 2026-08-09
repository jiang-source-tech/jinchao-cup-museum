from pathlib import Path

from core.xiaoxin.types import XiaoxinConfig, XiaoxinTurnResult, normalize_user_scope


def test_turn_result_defaults_are_safe():
    result = XiaoxinTurnResult.unhandled("existing_tool")

    assert result.handled is False
    assert result.reply is None
    assert result.route == {"reply_mode": "existing_tool"}
    assert result.bypass_reason == "existing_tool"


def test_config_from_dict_resolves_project_relative_paths(tmp_path):
    cfg = XiaoxinConfig.from_dict(
        {
            "enabled": True,
            "knowledge_dir": "data/xiaoxin_knowledge",
            "companion_db_path": "data/xiaoxin_companion.db",
            "max_tokens": 600,
            "free_chat_temperature": 0.7,
            "knowledge_temperature": 0.2,
            "boundary_temperature": 0.4,
        },
        project_dir=tmp_path,
    )

    assert cfg.enabled is True
    assert cfg.knowledge_dir == tmp_path / "data" / "xiaoxin_knowledge"
    assert cfg.companion_db_path == tmp_path / "data" / "xiaoxin_companion.db"
    assert cfg.max_tokens == 600
    assert cfg.free_chat_temperature == 0.7
    assert cfg.knowledge_temperature == 0.2
    assert cfg.boundary_temperature == 0.4


def test_normalize_user_scope_hashes_unsafe_values():
    assert normalize_user_scope("device-abc_123", "fallback") == "device-abc_123"
    assert normalize_user_scope("", "fallback") == "fallback"
    hashed = normalize_user_scope("student@example.com", "fallback")
    assert hashed.startswith("user_")
    assert len(hashed) == 37
