import os
from collections.abc import Mapping
from pathlib import Path

import yaml


LEGACY_CONFIG_SECTIONS = frozenset(
    {
        "voiceprint",
        "xiaoxin_control",
        "xiaoxin_runtime",
    }
)
LEGACY_DATA_NAMES = frozenset({"ota-inbox"})
LEGACY_DATA_PREFIXES = (
    "xiaoxin_",
    "xiaozhi_companion.db",
    "xiaozhi_control.db",
)


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

    enforce_museum_data_boundary()

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
    reject_legacy_config_sections(default_config, default_config_path)
    custom_config = (
        read_config(custom_config_path) if custom_config_path.exists() else {}
    )
    reject_legacy_config_sections(custom_config, custom_config_path)

    config = merge_configs(default_config, custom_config)
    # 初始化目录
    ensure_directories(config)

    # 缓存配置
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


def find_legacy_project_data(data_dir):
    """Return old-project entries that are forbidden in the active data mount."""
    root = Path(data_dir)
    if not root.exists():
        return ()

    matches = []
    for path in root.rglob("*"):
        normalized_name = path.name.lstrip(".").lower()
        if normalized_name in LEGACY_DATA_NAMES or normalized_name.startswith(
            LEGACY_DATA_PREFIXES
        ):
            matches.append(path)
    return tuple(sorted(matches, key=lambda path: path.name.lower()))


def enforce_museum_data_boundary(project_dir=None):
    """Refuse to start when the active mount contains old-project data."""
    root = Path(project_dir) if project_dir is not None else Path(get_project_dir())
    data_root = root / "data"
    legacy_entries = find_legacy_project_data(data_root)
    if not legacy_entries:
        return ()

    names = ", ".join(
        path.relative_to(data_root).as_posix() for path in legacy_entries
    )
    raise RuntimeError(
        "活动 data 目录包含旧项目数据，拒绝启动："
        f"{names}。请改用独立的博物馆数据目录；"
        "旧数据只能保存在活动挂载范围之外的归档中。"
    )


def reject_legacy_config_sections(config, config_path):
    """Reject configuration sections that can only belong to the old business."""
    legacy_sections = sorted(
        str(key) for key in config if str(key) in LEGACY_CONFIG_SECTIONS
    )
    if not legacy_sections:
        return

    names = ", ".join(legacy_sections)
    raise ValueError(
        f"配置文件包含已禁止的旧项目业务段：{names} ({config_path})。"
        "请仅迁移博物馆运行所需配置，不得整体复用旧项目配置。"
    )


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
