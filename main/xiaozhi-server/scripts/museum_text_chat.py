from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any
import uuid


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.logger import setup_logging  # noqa: E402
from config.settings import load_config  # noqa: E402
from core.business_runtime_factory import create_conversation_runtime  # noqa: E402
from core.conversation_runtime import (  # noqa: E402
    ConversationRuntime,
    TurnOutcome,
    TurnRequest,
)
from core.utils.modules_initialize import initialize_modules  # noqa: E402


LOGGER = setup_logging()
COMMANDS = {
    "/help": "显示命令",
    "/reset": "清空当前游客会话和对话历史",
    "/audit": "显示上一轮完整审计结果",
    "/quit": "退出",
}


def initialize_chat_llm(
    config: dict[str, Any],
    *,
    disabled: bool = False,
    required: bool = False,
) -> tuple[Any | None, str]:
    if disabled:
        if required:
            raise RuntimeError("不能同时使用 --no-llm 和 --require-llm")
        return None, "deterministic-only"

    selected = config.get("selected_module", {})
    provider_name = selected.get("LLM") if isinstance(selected, dict) else None
    if not provider_name:
        if required:
            raise RuntimeError(
                "当前服务配置没有 selected_module.LLM，无法执行真实 LLM 对话验收"
            )
        return None, "deterministic-only"

    providers = config.get("LLM", {})
    if not isinstance(providers, dict) or provider_name not in providers:
        raise RuntimeError(f"selected_module.LLM={provider_name} 没有对应配置")

    modules = initialize_modules(LOGGER, config, init_llm=True)
    llm = modules.get("llm")
    if llm is None:
        raise RuntimeError(f"LLM 提供方 {provider_name} 初始化后没有返回实例")
    return llm, f"llm:{provider_name}"


@dataclass
class MuseumTextChatSession:
    runtime: ConversationRuntime
    llm: Any | None
    device_prefix: str = "museum-text-chat"
    transport_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    visitor_session_id: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    last_outcome: TurnOutcome | None = None
    turn_number: int = 0
    _instance_id: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)
    _generation: int = 1

    @property
    def device_id(self) -> str:
        return f"{self.device_prefix}-{self._instance_id}-{self._generation}"

    def ask(self, text: str) -> TurnOutcome:
        question = text.strip()
        if not question:
            raise ValueError("对话文本不能为空")
        self.turn_number += 1
        outcome = self.runtime.handle_turn(
            TurnRequest(
                request_id=f"text-chat-{self.turn_number}-{uuid.uuid4().hex}",
                transport_session_id=self.transport_session_id,
                visitor_session_id=self.visitor_session_id,
                device_id=self.device_id,
                user_text=question,
                history=tuple(self.history[-8:]),
                occurred_at=datetime.now().astimezone(),
                llm=self.llm,
                metadata={"client": "museum_text_chat"},
            )
        )
        visitor_session_id = outcome.audit_record.get("visitor_session_id")
        if visitor_session_id:
            self.visitor_session_id = str(visitor_session_id)
        self.history.append({"role": "user", "content": question})
        self.history.append(
            {"role": "assistant", "content": outcome.spoken_text or ""}
        )
        self.history = self.history[-8:]
        self.last_outcome = outcome
        return outcome

    def reset(self) -> None:
        self._generation += 1
        self.transport_session_id = uuid.uuid4().hex
        self.visitor_session_id = None
        self.history.clear()
        self.last_outcome = None
        self.turn_number = 0


def outcome_payload(outcome: TurnOutcome) -> dict[str, Any]:
    context = outcome.display_state.get("context", {})
    return {
        "answer": outcome.spoken_text or "",
        "knowledge_status": outcome.knowledge_status,
        "exhibit_id": context.get("exhibit_id", ""),
        "exhibit_name": context.get("exhibit_name", ""),
        "context_source": context.get("source", ""),
        "coarse_intent": outcome.audit_record.get("coarse_intent", ""),
        "fine_intent": outcome.audit_record.get("fine_intent", ""),
        "intent_confidence": outcome.audit_record.get("intent_confidence", 0),
        "fact_ids": list(outcome.fact_ids),
        "source_ids": list(outcome.source_ids),
        "audit_id": outcome.audit_id,
        "error_code": outcome.error_code,
    }


def print_outcome(outcome: TurnOutcome, *, as_json: bool = False) -> None:
    payload = outcome_payload(outcome)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(f"小芯> {payload['answer']}")
    print(
        "     "
        f"状态={payload['knowledge_status']} "
        f"展品={payload['exhibit_name'] or '-'} "
        f"意图={payload['coarse_intent']}/{payload['fine_intent']} "
        f"事实={payload['fact_ids'] or '-'} "
        f"审计={payload['audit_id'] or '-'}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="博物馆服务端文本对话控制台")
    parser.add_argument(
        "--device-id",
        default="museum-text-chat",
        help="对话设备 ID 前缀",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="强制使用规则和事实检索，不初始化 LLM",
    )
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="没有可用 LLM 配置时启动失败",
    )
    parser.add_argument(
        "--once",
        help="只执行一轮文本并退出，适合自动化冒烟测试",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出单行 JSON",
    )
    return parser


def _print_help() -> None:
    for command, description in COMMANDS.items():
        print(f"{command:<8} {description}")


def run_console(args: argparse.Namespace) -> int:
    config = load_config()
    llm, mode = initialize_chat_llm(
        config,
        disabled=args.no_llm,
        required=args.require_llm,
    )
    runtime = create_conversation_runtime(config)
    session = MuseumTextChatSession(
        runtime=runtime,
        llm=llm,
        device_prefix=args.device_id,
    )

    if args.once:
        print_outcome(session.ask(args.once), as_json=args.json)
        return 0

    print(f"博物馆文本对话控制台已启动，模式：{mode}")
    if mode == "deterministic-only":
        print("当前没有调用真实 LLM；回答只使用规则、检索和已发布事实。")
    _print_help()

    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text == "/quit":
            return 0
        if text == "/help":
            _print_help()
            continue
        if text == "/reset":
            session.reset()
            print("会话已重置。")
            continue
        if text == "/audit":
            if session.last_outcome is None:
                print("还没有可显示的审计记录。")
            else:
                print(
                    json.dumps(
                        dict(session.last_outcome.audit_record),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            continue
        print_outcome(session.ask(text), as_json=args.json)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_console(args)
    except (RuntimeError, ValueError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
