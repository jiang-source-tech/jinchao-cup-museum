from queue import Queue
from types import SimpleNamespace

import pytest

from core.business_runtime_factory import create_conversation_runtime
from core.connection import ConnectionHandler
from core.conversation_runtime import TurnOutcome
from core.providers.tts.dto.dto import ContentType, SentenceType


def test_connection_commits_business_runtime_spoken_text_to_existing_tts_queue():
    with pytest.raises(ValueError, match="only business_runtime.type=museum"):
        create_conversation_runtime({"business_runtime": {"type": "legacy"}})

    requests = []

    class Runtime:
        def handle_turn(self, request):
            requests.append(request)
            return TurnOutcome(
                handled=True,
                spoken_text="这是博物馆回答。",
                museum_state={"type": "museum_state", "version": 1},
                audit_record={"visitor_session_id": "visitor-1"},
            )

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
    conn.visitor_session_id = None
    conn.last_museum_state = None
    conn.device_id = "device-1"
    conn.device_time_snapshot = None
    conn.llm = None
    conn.config = {}
    published = []
    conn._publish_retrieving_state = lambda _request_id: None
    conn._publish_museum_state = lambda state: published.append(state)
    conn.logger = SimpleNamespace(
        bind=lambda **kwargs: SimpleNamespace(error=lambda *args, **kw: None)
    )

    handled = conn._try_business_turn("它是什么材料做的？", "sentence-1")

    assert handled is True
    assert requests[0].request_id == "sentence-1"
    assert requests[0].history == ()
    assert requests[0].visitor_session_id is None
    assert conn.visitor_session_id == "visitor-1"
    assert published == [{"type": "museum_state", "version": 1}]
    assert conn.tts.stored == [("sentence-1", "这是博物馆回答。")]
    queued = conn.tts.tts_text_queue.get_nowait()
    assert queued.sentence_type == SentenceType.MIDDLE
    assert queued.content_type == ContentType.TEXT
    assert queued.content_detail == "这是博物馆回答。"
    assert conn.dialogue.messages[-1].content == "这是博物馆回答。"
