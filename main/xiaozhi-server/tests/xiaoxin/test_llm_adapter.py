from core.xiaoxin.llm_adapter import LLMChatAdapter


class LegacyStreamingLLM:
    def __init__(self):
        self.calls = []

    def response(self, session_id, dialogue):
        self.calls.append((session_id, dialogue))
        yield "小芯"
        yield "收到"


def test_llm_adapter_retries_provider_without_sampling_kwargs():
    llm = LegacyStreamingLLM()
    adapter = LLMChatAdapter(llm, "session_1")

    reply = adapter.complete_chat(
        [{"role": "user", "content": "你好"}],
        max_tokens=300,
        temperature=0.5,
    )

    assert reply == "小芯收到"
    assert llm.calls == [("session_1", [{"role": "user", "content": "你好"}])]


def test_llm_adapter_forwards_json_response_format():
    class StructuredLLM:
        def __init__(self):
            self.kwargs = None

        def response(self, session_id, dialogue, **kwargs):
            self.kwargs = kwargs
            yield '{"proposals":[]}'

    llm = StructuredLLM()
    reply = LLMChatAdapter(llm, "session-json").complete_chat(
        [{"role": "user", "content": "返回 JSON"}],
        max_tokens=1000,
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    assert reply == '{"proposals":[]}'
    assert llm.kwargs["response_format"] == {"type": "json_object"}


class MemoryToolStreamingLLM:
    def __init__(self, *, call_tool: bool) -> None:
        self.call_tool = call_tool
        self.function_calls = []
        self.response_calls = []

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        self.function_calls.append((session_id, dialogue, functions, kwargs))
        if not self.call_tool:
            yield "不用回忆也能回答。", None
            return
        yield None, [
            {
                "id": "call-memory-1",
                "function": {
                    "name": "recall_companion_memory",
                    "arguments": '{"query":"上次让我紧张的考试"',
                },
            }
        ]
        yield None, [{"function": {"arguments": "}"}}]

    def response(self, session_id, dialogue, **kwargs):
        self.response_calls.append((session_id, dialogue, kwargs))
        yield "你之前提到六级考试会让你紧张。"


def test_memory_tool_completion_does_not_add_a_second_call_when_not_requested():
    llm = MemoryToolStreamingLLM(call_tool=False)
    adapter = LLMChatAdapter(llm, "session-memory")
    handler_calls = []

    reply = adapter.complete_chat_with_memory_tool(
        [{"role": "user", "content": "今天聊点别的"}],
        lambda arguments: handler_calls.append(arguments) or {"memories": ()},
    )

    assert reply == "不用回忆也能回答。"
    assert handler_calls == []
    assert llm.response_calls == []


def test_memory_tool_completion_executes_one_bounded_round_trip():
    llm = MemoryToolStreamingLLM(call_tool=True)
    adapter = LLMChatAdapter(llm, "session-memory")
    handler_calls = []

    reply = adapter.complete_chat_with_memory_tool(
        [{"role": "user", "content": "上次让我紧张的考试是什么？"}],
        lambda arguments: handler_calls.append(arguments)
        or {"memories": ("用户曾说六级考试让自己紧张。",)},
        max_tokens=120,
        temperature=0.2,
    )

    assert reply == "你之前提到六级考试会让你紧张。"
    assert handler_calls == [{"query": "上次让我紧张的考试"}]
    assert len(llm.function_calls) == 1
    assert len(llm.response_calls) == 1
    followup = llm.response_calls[0][1]
    assert followup[-2]["tool_calls"][0]["function"]["name"] == (
        "recall_companion_memory"
    )
    assert "用户曾说六级考试让自己紧张" in followup[-1]["content"]
