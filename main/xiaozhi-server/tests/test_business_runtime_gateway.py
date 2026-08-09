from datetime import datetime
from queue import Queue
from types import SimpleNamespace

from core.business_runtime_factory import create_conversation_runtime
from core.connection import ConnectionHandler
from core.conversation_runtime import TurnOutcome, TurnRequest
from core.providers.tts.dto.dto import ContentType, SentenceType


def test_legacy_runtime_adapter_routes_turn_and_preserves_committed_output():
    calls = []

    def legacy_handler(user_text, request_id):
        calls.append((user_text, request_id))
        return True

    runtime = create_conversation_runtime(
        {"business_runtime": {"type": "legacy"}},
        legacy_turn_handler=legacy_handler,
    )

    outcome = runtime.handle_turn(
        TurnRequest(
            request_id="sentence-1",
            transport_session_id="transport-1",
            visitor_session_id=None,
            device_id="device-1",
            user_text="你好",
            history=(),
            occurred_at=datetime.now().astimezone(),
            llm=None,
        )
    )

    assert calls == [("你好", "sentence-1")]
    assert outcome.handled is True
    assert outcome.output_committed is True
    assert outcome.spoken_text is None


def test_connection_commits_business_runtime_spoken_text_to_existing_tts_queue():
    requests = []

    class Runtime:
        def handle_turn(self, request):
            requests.append(request)
            return TurnOutcome(handled=True, spoken_text="这是博物馆回答。")

    class Dialogue:
        def __init__(self):
            self.messages = []

        def get_llm_dialogue(self):
            return [{"role": "user", "content": "它是什么材料做的？"}]

        def put(self, message):
            self.messages.append(message)

    class TTS:
        def __init__(self):
            self.stored = []
            self.tts_text_queue = Queue()

        def store_tts_text(self, sentence_id, text):
            self.stored.append((sentence_id, text))

    conn = ConnectionHandler.__new__(ConnectionHandler)
    conn.conversation_runtime = Runtime()
    conn.dialogue = Dialogue()
    conn.tts = TTS()
    conn.session_id = "transport-1"
    conn.device_id = "device-1"
    conn.current_speaker = None
    conn.device_time_snapshot = None
    conn.llm = None
    conn.config = {}
    conn.logger = SimpleNamespace(
        bind=lambda **kwargs: SimpleNamespace(error=lambda *args, **kw: None)
    )

    handled = conn._try_business_turn("它是什么材料做的？", "sentence-1")

    assert handled is True
    assert requests[0].request_id == "sentence-1"
    assert requests[0].history == ()
    assert conn.tts.stored == [("sentence-1", "这是博物馆回答。")]
    queued = conn.tts.tts_text_queue.get_nowait()
    assert queued.sentence_type == SentenceType.MIDDLE
    assert queued.content_type == ContentType.TEXT
    assert queued.content_detail == "这是博物馆回答。"
    assert conn.dialogue.messages[-1].content == "这是博物馆回答。"
