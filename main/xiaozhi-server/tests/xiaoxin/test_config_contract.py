from pathlib import Path
import json

import config.config_loader as config_loader
from core.utils.cache.config import CacheType
from core.utils.cache.manager import cache_manager
from ruamel.yaml import YAML


PROJECT_DIR = Path(__file__).resolve().parents[2]


def load_config():
    return load_config_file("config.yaml")


def load_config_file(config_name):
    yaml = YAML()
    with open(PROJECT_DIR / config_name, "r", encoding="utf-8") as f:
        return yaml.load(f)


def test_xiaoxin_runtime_config_is_enabled():
    cfg = load_config()

    assert cfg["xiaoxin_runtime"]["enabled"] is True
    assert cfg["xiaoxin_runtime"]["knowledge_dir"] == "data/xiaoxin_knowledge"
    assert cfg["xiaoxin_runtime"]["companion_db_path"] == "data/xiaoxin_companion.db"
    assert "memory_dir" not in cfg["xiaoxin_runtime"]
    assert "companion_memory_v2_enabled" not in cfg["xiaoxin_runtime"]
    assert cfg["xiaoxin_runtime"]["companion_db_path"] == "data/xiaoxin_companion.db"
    assert cfg["xiaoxin_runtime"]["companion_worker_enabled"] is False
    assert cfg["xiaoxin_runtime"]["companion_memory_interpreter_mode"] == "off"
    assert cfg["xiaoxin_runtime"][
        "companion_memory_active_explicit_release_enabled"
    ] is False
    assert cfg["xiaoxin_runtime"]["companion_worker_tick_seconds"] == 30
    assert cfg["xiaoxin_compliance"] == {
        "enabled": True,
        "companion_service_mode": "tool_only",
        "current_service_agreement_version": "service-2026-08-v1",
        "current_privacy_policy_version": "privacy-2026-08-v1",
        "current_risk_notice_version": "risk-2026-08-v1",
        "guardian_invitation_ttl_seconds": 600,
    }


def test_semantic_memory_release_mode_is_declared_and_fail_closed_by_default():
    runtime = load_config_file("config.yaml")["xiaoxin_runtime"]
    assert runtime["companion_memory_interpreter_mode"] in {
        "off",
        "shadow",
        "candidate",
        "active_explicit",
    }
    assert runtime["companion_memory_interpreter_mode"] == "off"
    assert runtime["companion_memory_active_explicit_release_enabled"] is False


def test_controlled_deployment_enables_explicit_memory_and_initiative_delivery():
    deployment = load_config_file("data/.config.yaml")
    runtime = deployment["xiaoxin_runtime"]
    compliance = deployment["xiaoxin_compliance"]

    assert deployment["selected_module"]["LLM"] == "AliLLM"
    assert runtime["companion_worker_llm"] == "DeepSeekLLM"
    assert runtime["companion_worker_enabled"] is True
    assert runtime["companion_memory_interpreter_mode"] == "active_explicit"
    assert runtime["companion_memory_active_explicit_release_enabled"] is True
    assert runtime["companion_initiative_scheduler_enabled"] is True
    assert runtime["companion_initiative_delivery_enabled"] is True
    assert compliance["enabled"] is True
    assert compliance["companion_service_mode"] == "tool_only"
    deepseek = deployment["LLM"]["DeepSeekLLM"]
    assert deepseek["base_url"] == "https://api.deepseek.com"
    assert deepseek["model_name"] == "deepseek-v4-flash"
    assert deepseek["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "api_key" not in deepseek
    assert deployment["ASR"]["AliyunBLStreamASR"] == {
        "api_key_env": "DASHSCOPE_API_KEY",
        "model": "paraformer-realtime-v2",
    }
    assert deployment["LLM"]["AliLLM"]["api_key_env"] == "DASHSCOPE_API_KEY"
    assert deployment["VLLM"]["QwenVLVLLM"]["api_key_env"] == "DASHSCOPE_API_KEY"
    assert deployment["TTS"]["AliBLTTS"]["api_key_env"] == "DASHSCOPE_API_KEY"


def test_prompt_and_tts_are_senior_sister():
    cfg = load_config()

    assert "数字学姐" in cfg["prompt"]
    assert "数字学长" not in cfg["prompt"]
    assert "学长学姐" in cfg["prompt"]
    assert "不能替用户联系老师、辅导员、学长学姐或任何真实个人" in cfg["prompt"]
    assert "数字学姐" in cfg["TTS"]["AliBLTTS"]["instructions"]
    assert "撒娇" not in cfg["TTS"]["AliBLTTS"]["instructions"]


def test_prompt_includes_synced_companion_persona_rules():
    cfg = load_config()

    assert "数字学姐" in cfg["prompt"]
    assert "数字学长" not in cfg["prompt"]
    for required in (
        "安静陪伴",
        "罗杰斯式情绪陪伴",
        "电子宠物身体感",
        "克制的亲近感",
    ):
        assert required in cfg["prompt"]


def test_qwen_realtime_tts_uses_realtime_provider_in_all_local_configs():
    yaml = YAML()

    for config_name in ("config.yaml", "data/.config.yaml"):
        with open(PROJECT_DIR / config_name, "r", encoding="utf-8") as f:
            cfg = yaml.load(f)

        tts_cfg = cfg["TTS"]["AliBLTTS"]
        assert tts_cfg["model"] == "qwen3-tts-instruct-flash-realtime"
        assert tts_cfg["type"] == "qwen_realtime"
        assert tts_cfg["volume"] == 100


def test_user_facing_persona_strings_do_not_revert_to_xiaozhi():
    cfg = load_config()

    assert "小智" not in cfg["system_error_response"]
    assert "小芯" in cfg["system_error_response"]


def test_effective_config_keeps_xiaoxin_persona(monkeypatch):
    cache_manager.clear(CacheType.CONFIG)
    monkeypatch.setenv(
        "XIAOXIN_VOICEPRINT_URL",
        "http://voiceprint-api:8005/voiceprint/health?key=test-only",
    )

    cfg = config_loader.load_config()
    tts_cfg = cfg["TTS"][cfg["selected_module"]["TTS"]]

    assert cfg["xiaoxin_runtime"]["enabled"] is True
    assert cfg["xiaoxin_runtime"]["companion_worker_enabled"] is True
    assert cfg["selected_module"]["LLM"] == "AliLLM"
    assert cfg["xiaoxin_runtime"]["companion_worker_llm"] == "DeepSeekLLM"
    assert cfg["xiaoxin_runtime"]["companion_memory_interpreter_mode"] == "active_explicit"
    assert cfg["xiaoxin_runtime"][
        "companion_memory_active_explicit_release_enabled"
    ] is True
    assert cfg["xiaoxin_runtime"]["companion_initiative_scheduler_enabled"] is True
    assert cfg["xiaoxin_runtime"]["companion_initiative_delivery_enabled"] is True
    assert "\u6570\u5b57\u5b66\u59d0" in cfg["prompt"]
    assert "\u6570\u5b57\u5b66\u59d0" in tts_cfg["instructions"]
    assert "\u6492\u5a07" not in tts_cfg["instructions"]
    assert cfg["voiceprint"]["url"].endswith("key=test-only")


def test_first_release_tenant_and_doorbell_config_keys_are_declared():
    for config_name in ("config.yaml", "data/.config.yaml"):
        cfg = load_config_file(config_name)
        control = cfg["xiaoxin_control"]

        assert control["tenant"]["id"] == "hzcu-iee"
        assert control["tenant"]["display_name"] == "信息与电气工程学院"
        assert "endpoint" in control["doorbell_mqtt"]
        assert "username" in control["doorbell_mqtt"]
        assert "password" in control["doorbell_mqtt"]
        assert control["doorbell_mqtt"]["keepalive_seconds"] == 240
        assert control["doorbell_mqtt"]["qos"] == 1


def test_reliable_notification_tts_defaults_are_declared_everywhere():
    expected_scalars = {
        "tts_ready_ack_timeout_ms": 700,
        "tts_done_ack_timeout_ms": 10000,
    }
    expected_lists = {
        "tts_ready_start_retry_delays_ms": [300, 600, 1200],
        "tts_delivery_retry_delays_ms": [2000, 5000, 15000, 30000],
    }

    for config_name in ("config.yaml", "data/.config.yaml"):
        cfg = load_config_file(config_name)
        assert "tts_done_timeout_seconds" not in cfg["xiaoxin_control"]

        for key, expected in expected_scalars.items():
            assert type(cfg[key]) is int
            assert cfg[key] == expected

        for key, expected in expected_lists.items():
            assert isinstance(cfg[key], list)
            assert cfg[key] == expected
            assert all(type(item) is int for item in cfg[key])


def test_todo_reminder_replay_window_is_declared_everywhere():
    for config_name in ("config.yaml", "data/.config.yaml"):
        control = load_config_file(config_name)["xiaoxin_control"]
        assert control["todo_reminder_replay_window_minutes"] == 120


def test_ack_contract_documents_all_firmware_error_reasons():
    contract = (
        PROJECT_DIR.parents[1] / "docs/development/xiaoxin-tts-playback-ack.md"
    ).read_text(encoding="utf-8")
    for reason in (
        "preroll_overflow",
        "pipeline_reset_timeout",
        "drain_task_create_failed",
        "playback_drain_timeout",
        "superseded",
        "stale_start",
        "decoder_create_failed",
        "decode_failed",
        "resampler_create_failed",
        "output_write_timeout",
    ):
        assert f"`{reason}`" in contract


def test_overview_mqtt_defaults_are_explicit_and_disabled():
    cfg = load_config()
    overview = cfg["xiaoxin_control"]["overview_mqtt"]
    amap_api_key = overview["amap_api_key"]

    assert amap_api_key == ""
    assert overview == {
        "enabled": False,
        "db": "data/xiaoxin_overview.db",
        "ip_hmac_secret": "",
        "trusted_proxy_cidrs": [],
        "retry_tick_seconds": 1,
        "daily_refresh_hour": 0,
        "daily_refresh_minute": 5,
        "weather_provider": "amap",
        "amap_api_host": "restapi.amap.com",
        "amap_api_key": amap_api_key,
        "amap_city_adcodes": {"浙江/杭州": "330100"},
    }


def test_amap_weather_config_is_declared_in_local_configs():
    configured_keys = []
    for config_name in ("config.yaml", "data/.config.yaml"):
        overview = load_config_file(config_name)["xiaoxin_control"][
            "overview_mqtt"
        ]
        assert overview["weather_provider"] == "amap"
        assert overview["amap_api_host"] == "restapi.amap.com"
        assert overview["amap_city_adcodes"] == {"浙江/杭州": "330100"}
        assert overview["amap_api_key"] == ""
        configured_keys.append(overview["amap_api_key"])
    assert configured_keys[0] == configured_keys[1]


def test_native_voice_weather_tool_replaces_legacy_qweather_plugin():
    for config_name in ("config.yaml", "data/.config.yaml"):
        cfg = load_config_file(config_name)
        assert "get_weather" not in cfg.get("plugins", {})

    cfg = load_config()
    for intent_name in ("intent_llm", "function_call"):
        functions = cfg["Intent"][intent_name]["functions"]
        assert "get_weather" not in functions
        assert "get_xiaoxin_weather" in functions

    assert not (PROJECT_DIR / "plugins_func/functions/get_weather.py").exists()
    assert (PROJECT_DIR / "plugins_func/functions/get_xiaoxin_weather.py").exists()

    prompt_template = (PROJECT_DIR / "agent-base-prompt.txt").read_text(
        encoding="utf-8"
    )
    assert "get_weather" not in prompt_template
    assert "weather_info" not in prompt_template
    assert "get_xiaoxin_weather" in prompt_template

    prompt_manager = (PROJECT_DIR / "core/utils/prompt_manager.py").read_text(
        encoding="utf-8"
    )
    assert "plugins_func.functions.get_weather" not in prompt_manager


def test_voice_weather_does_not_require_node_mcp_runtime():
    settings_path = PROJECT_DIR / "data/.mcp_server_settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings == {"mcpServers": {}}

    prompt_template = (PROJECT_DIR / "agent-base-prompt.txt").read_text(
        encoding="utf-8"
    )
    assert "get_xiaoxin_weather" in prompt_template
    assert "geocoding" not in prompt_template
    assert "weather_forecast" not in prompt_template
    assert "daily_city_weather" not in prompt_template


def test_local_config_merge_preserves_control_storage_without_manager_services():
    base = {
        "server": {"http_port": 8003},
        "xiaoxin_control": {
            "identity_db": "local-identity.db",
            "overview_mqtt": {"enabled": False, "db": "local-overview.db"},
        },
    }
    override = {
        "server": {"websocket": "wss://device.example/xiaoxin/v1/"},
        "xiaoxin_control": {"overview_mqtt": {"enabled": True}},
    }

    merged = config_loader.merge_configs(base, override)

    assert merged["server"] == {
        "http_port": 8003,
        "websocket": "wss://device.example/xiaoxin/v1/",
    }
    assert merged["xiaoxin_control"] == {
        "identity_db": "local-identity.db",
        "overview_mqtt": {"enabled": True, "db": "local-overview.db"},
    }
    assert "manager-api" not in merged
    assert "read_config_from_api" not in merged


def test_deployment_config_enables_overview_without_committing_hmac_secret():
    cfg = load_config_file("data/.config.yaml")
    overview = cfg["xiaoxin_control"]["overview_mqtt"]

    assert overview["enabled"] is True
    assert overview["db"] == "data/xiaoxin_overview.db"
    assert overview["ip_hmac_secret"] == ""


def test_deployment_config_declares_the_controlled_ip_ota_endpoint():
    cfg = load_config_file("data/.config.yaml")

    assert cfg["xiaoxin_control"]["ota_release"] == {
        "public_ota_url": "http://121.43.33.0:8003/xiaoxin/ota/",
        "db": "data/xiaoxin_firmware_releases.db",
        "artifact_dir": "data/xiaoxin_firmware",
        "default_channel": "stable",
        "allow_insecure_http": True,
        "legacy_filename_fallback": False,
    }


def test_docker_compose_passes_overview_hmac_secret_from_environment():
    expected = (
        "XIAOXIN_OVERVIEW_IP_HMAC_SECRET="
        "${XIAOXIN_OVERVIEW_IP_HMAC_SECRET:-}"
    )

    for compose_name in ("docker-compose.yml",):
        compose = (PROJECT_DIR / compose_name).read_text(encoding="utf-8")
        assert expected in compose


def test_docker_compose_uses_shanghai_timezone_for_business_dates():
    for compose_name in ("docker-compose.yml",):
        compose = (PROJECT_DIR / compose_name).read_text(encoding="utf-8")
        assert "- TZ=UTC" not in compose
        assert "- TZ=Asia/Shanghai" in compose


def test_docker_compose_passes_amap_weather_overrides_from_environment():
    expected_lines = (
        "XIAOXIN_AMAP_API_KEY=${XIAOXIN_AMAP_API_KEY:-}",
        "XIAOXIN_AMAP_API_HOST=${XIAOXIN_AMAP_API_HOST:-}",
        "DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY:-}",
    )

    for compose_name in ("docker-compose.yml",):
        compose = (PROJECT_DIR / compose_name).read_text(encoding="utf-8")
        for expected in expected_lines:
            assert expected in compose
