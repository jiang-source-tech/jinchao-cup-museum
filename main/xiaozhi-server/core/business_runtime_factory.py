from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from core.conversation_runtime import ConversationRuntime
from core.museum.runtime import MuseumRuntime
from core.museum.store import MuseumStore


def create_conversation_runtime(
    config: Mapping[str, Any],
) -> ConversationRuntime:
    runtime_config = config.get("business_runtime", {})
    if not isinstance(runtime_config, Mapping):
        raise ValueError("business_runtime must be a mapping")

    runtime_type = str(runtime_config.get("type", "museum")).strip().lower()
    if runtime_type != "museum":
        raise ValueError("only business_runtime.type=museum is supported")
    exhibit_context_mode = str(
        runtime_config.get("exhibit_context_mode", "explicit")
    ).strip().lower()
    if exhibit_context_mode not in {"explicit", "demo_placement"}:
        raise ValueError(
            "business_runtime.exhibit_context_mode must be explicit or demo_placement"
        )
    database_path = Path(
        str(runtime_config.get("database_path", "data/museum_demo.db"))
    )
    store = MuseumStore(database_path)
    store.seed_demo_content()
    demo_device_id = str(runtime_config.get("demo_device_id", "")).strip()
    if demo_device_id and exhibit_context_mode == "demo_placement":
        store.ensure_demo_placement(
            demo_device_id,
            datetime.now().astimezone(),
        )
    return MuseumRuntime(
        store,
        auto_assign_unknown_devices=bool(
            runtime_config.get("auto_assign_unknown_devices", False)
        ),
        exhibit_context_mode=exhibit_context_mode,
    )
