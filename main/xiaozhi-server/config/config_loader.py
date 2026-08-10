import os
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml


def get_project_dir():
    """获取项目根目录"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, Mapping):
        raise ValueError(f"配置文件必须是 YAML 对象: {config_path}")
    return dict(config)


def load_config():
    """加载配置文件"""
    from core.utils.cache.manager import cache_manager, CacheType

    warn_if_legacy_data_present()

    # 检查缓存
    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config

    project_dir = Path(get_project_dir())
    local_default_path = project_dir / "config.yaml"
    tracked_default_path = project_dir / "config.example.yaml"
    custom_config_path = project_dir / "data/.config.yaml"

    # 本地 config.yaml 优先；干净检出使用无密钥的受版本控制模板。
    default_config_path = (
        local_default_path if local_default_path.exists() else tracked_default_path
    )
    if not default_config_path.exists():
        raise FileNotFoundError(
            "找不到 config.yaml 或 config.example.yaml，无法加载服务配置"
        )
    default_config = read_config(default_config_path)
    custom_config = (
        read_config(custom_config_path) if custom_config_path.exists() else {}
    )

    config = merge_configs(default_config, custom_config)
    # 初始化目录
    ensure_directories(config)

    # 缓存配置
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


def find_legacy_xiaoxin_databases(data_dir):
    """Return legacy databases that must not remain in the active data mount."""
    return tuple(sorted(Path(data_dir).glob("xiaoxin_*.db")))


def warn_if_legacy_data_present(project_dir=None):
    """Emit a startup-visible warning when an old project database is mounted."""
    root = Path(project_dir) if project_dir is not None else Path(get_project_dir())
    legacy_databases = find_legacy_xiaoxin_databases(root / "data")
    if not legacy_databases:
        return ()

    names = ", ".join(path.name for path in legacy_databases)
    print(
        "CRITICAL: 活动 data 目录检测到旧项目数据库："
        f"{names}。当前博物馆运行时不会读取这些数据库；"
        "部署前必须确认数据挂载并完成隔离或归档。",
        file=sys.stderr,
    )
    return legacy_databases

def ensure_directories(config):
    """确保所有配置路径存在"""
    dirs_to_create = set()
    project_dir = get_project_dir()  # 获取项目根目录
    # 日志文件目录
    log_dir = config.get("log", {}).get("log_dir", "tmp")
    dirs_to_create.add(os.path.join(project_dir, log_dir))

    # ASR/TTS模块输出目录
    for module in ["ASR", "TTS"]:
        if config.get(module) is None:
            continue
        for provider in config.get(module, {}).values():
            output_dir = provider.get("output_dir", "")
            if output_dir:
                dirs_to_create.add(output_dir)

    # 根据selected_module创建模型目录
    selected_modules = config.get("selected_module", {})
    for module_type in ["ASR", "LLM", "TTS"]:
        selected_provider = selected_modules.get(module_type)
        if not selected_provider:
            continue
        if config.get(module_type) is None:
            continue
        if config.get(selected_provider) is None:
            continue
        provider_config = config.get(module_type, {}).get(selected_provider, {})
        output_dir = provider_config.get("output_dir")
        if output_dir:
            full_model_dir = os.path.join(project_dir, output_dir)
            dirs_to_create.add(full_model_dir)

    # 统一创建目录（保留原data目录创建）
    for dir_path in dirs_to_create:
        try:
            os.makedirs(dir_path, exist_ok=True)
        except PermissionError:
            print(f"警告：无法创建目录 {dir_path}，请检查写入权限")


def merge_configs(default_config, custom_config):
    """
    递归合并配置，custom_config优先级更高

    Args:
        default_config: 默认配置
        custom_config: 用户自定义配置

    Returns:
        合并后的配置
    """
    if not isinstance(default_config, Mapping) or not isinstance(
        custom_config, Mapping
    ):
        return custom_config

    merged = dict(default_config)

    for key, value in custom_config.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value

    return merged
