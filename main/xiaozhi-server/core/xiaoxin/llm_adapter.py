from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
from typing import Callable, Mapping


RECALL_COMPANION_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "recall_companion_memory",
        "description": (
            "仅当回答当前用户需要回忆其过去明确说过的个人经历、偏好、目标或"
            "近况时调用。一般知识问答、闲聊或无需过去信息时不要调用。"
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "maxLength": 500},
                "fact_keys": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
                "kinds": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "string",
                        "enum": [
                            "profile",
                            "goal",
                            "preference",
                            "interest",
                            "life_event",
                            "relationship_context",
                            "wellbeing",
                            "profile_fact",
                            "explicit_preference",
                            "explicit_boundary",
                            "user_life_event",
                            "goal_completed",
                            "future_event",
                        ],
                    },
                },
                "exclude_sensitivities": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {
                        "type": "string",
                        "enum": ["low", "private", "sensitive"],
                    },
                },
                "occurred_after": {"type": ["string", "null"]},
                "occurred_before": {"type": ["string", "null"]},
            },
            "required": ["query"],
        },
    },
}


class LLMChatAdapter:
    def __init__(self, llm, session_id: str):
        self.llm = llm
        self.session_id = session_id

    @property
    def supports_native_memory_tool(self) -> bool:
        return getattr(self.llm, "supports_native_function_calls", True) is not False

    def complete_chat(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> str:
        optional_kwargs = {
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            optional_kwargs["response_format"] = response_format
        if hasattr(self.llm, "complete_chat"):
            try:
                return self.llm.complete_chat(
                    messages,
                    **optional_kwargs,
                )
            except TypeError:
                return self.llm.complete_chat(messages)

        try:
            chunks = self.llm.response(
                self.session_id,
                messages,
                **optional_kwargs,
            )
        except TypeError:
            chunks = self.llm.response(self.session_id, messages)
        return "".join(chunk for chunk in chunks if chunk)

    def complete_chat_with_memory_tool(
        self,
        messages: list[dict],
        tool_handler: Callable[[Mapping[str, object]], Mapping[str, object]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout_seconds: float = 20.0,
    ) -> str:
        """Complete one response with at most one bounded memory-tool round trip."""
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            self._complete_chat_with_memory_tool,
            messages,
            tool_handler,
            max_tokens,
            temperature,
        )
        try:
            return future.result(timeout=max(float(timeout_seconds), 0.001))
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError("memory tool completion timed out") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _complete_chat_with_memory_tool(
        self,
        messages: list[dict],
        tool_handler: Callable[[Mapping[str, object]], Mapping[str, object]],
        max_tokens: int | None,
        temperature: float | None,
    ) -> str:
        try:
            responses = self.llm.response_with_functions(
                self.session_id,
                messages,
                functions=[RECALL_COMPANION_MEMORY_TOOL],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except TypeError:
            responses = self.llm.response_with_functions(
                self.session_id,
                messages,
                functions=[RECALL_COMPANION_MEMORY_TOOL],
            )
        content_parts: list[str] = []
        tool_call = {"id": "recall-companion-memory", "name": "", "arguments": ""}
        for response in responses:
            content, calls = _function_response_parts(response)
            if content:
                content_parts.append(content)
            for call in calls:
                _merge_tool_call(tool_call, call)
        if not tool_call["name"]:
            return "".join(content_parts)
        if tool_call["name"] != "recall_companion_memory":
            raise ValueError("unsupported internal tool request")
        try:
            arguments = json.loads(tool_call["arguments"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("memory tool arguments are invalid JSON") from exc
        if not isinstance(arguments, dict):
            raise ValueError("memory tool arguments must be an object")
        tool_result = dict(tool_handler(arguments))
        arguments_json = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        followup_messages = [
            *messages,
            {
                "role": "assistant",
                "content": "".join(content_parts) or None,
                "tool_calls": [
                    {
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {
                            "name": tool_call["name"],
                            "arguments": arguments_json,
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": tool_call["name"],
                "content": json.dumps(
                    tool_result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        return self.complete_chat(
            followup_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _function_response_parts(response: object) -> tuple[str, tuple[object, ...]]:
    if isinstance(response, dict):
        content = response.get("content")
        calls = response.get("tool_calls")
    elif isinstance(response, tuple) and len(response) == 2:
        content, calls = response
    else:
        content, calls = response, None
    text = content if isinstance(content, str) else ""
    if calls is None:
        return text, ()
    if isinstance(calls, (list, tuple)):
        return text, tuple(calls)
    return text, (calls,)


def _merge_tool_call(target: dict[str, str], call: object) -> None:
    if isinstance(call, dict):
        call_id = call.get("id")
        function = call.get("function", call)
        name = function.get("name") if isinstance(function, dict) else None
        arguments = (
            function.get("arguments") if isinstance(function, dict) else None
        )
    else:
        call_id = getattr(call, "id", None)
        function = getattr(call, "function", call)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
    if isinstance(call_id, str) and call_id:
        target["id"] = call_id
    if isinstance(name, str) and name:
        target["name"] += name
    if isinstance(arguments, str) and arguments:
        target["arguments"] += arguments
