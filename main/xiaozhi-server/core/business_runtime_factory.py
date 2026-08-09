from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from core.conversation_runtime import ConversationRuntime, TurnOutcome, TurnRequest
from core.museum.runtime import MuseumRuntime
from core.museum.store import MuseumStore


LegacyTurnHandler = Callable[[str, str], bool]


class LegacyCompanionRuntimeAdapter:
    """Transitional adapter for the existing connection-owned companion flow."""

    def __init__(self, handler: LegacyTurnHandler):
        self._handler = handler

    def handle_turn(self, request: TurnRequest) -> TurnOutcome:
        handled = self._handler(request.user_text, request.request_id)
        if not handled:
            return TurnOutcome.unhandled("legacy_unhandled")
        return TurnOutcome(handled=True, output_committed=True)


def create_conversation_runtime(
    config: Mapping[str, Any],
    *,
    legacy_turn_handler: LegacyTurnHandler,
) -> ConversationRuntime:
    runtime_config = config.get("business_runtime", {})
    if not isinstance(runtime_config, Mapping):
        raise ValueError("business_runtime must be a mapping")

    runtime_type = str(runtime_config.get("type", "legacy")).strip().lower()
    if runtime_type == "legacy":
        return LegacyCompanionRuntimeAdapter(legacy_turn_handler)
    if runtime_type == "museum":
        database_path = Path(
            str(runtime_config.get("database_path", "data/museum_demo.db"))
        )
        store = MuseumStore(database_path)
        store.seed_demo_content()
        demo_device_id = str(runtime_config.get("demo_device_id", "")).strip()
        if demo_device_id:
            store.ensure_demo_placement(
                demo_device_id,
                datetime.now().astimezone(),
            )
        return MuseumRuntime(
            store,
            auto_assign_unknown_devices=bool(
                runtime_config.get("auto_assign_unknown_devices", False)
            ),
        )
    raise ValueError(f"unsupported business_runtime.type: {runtime_type}")
