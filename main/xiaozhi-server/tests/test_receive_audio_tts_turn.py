import asyncio

from core.handle import receiveAudioHandle


def test_normal_voice_turn_allocates_sentence_id_before_tts_start(monkeypatch):
    events = []

    class ImmediateExecutor:
        def submit(self, callback, *args):
            callback(*args)

    class FakeConnection:
        need_bind = False
        max_output_size = 0
        client_is_speaking = False
        client_listen_mode = "auto"
        client_abort = True
        sentence_id = "previous-sentence"
        current_speaker = None
        headers = {}
        executor = ImmediateExecutor()

        def chat(self, query, depth=0, sentence_id=None):
            if sentence_id is None:
                self.sentence_id = "chat-generated-sentence"
            events.append(("chat", sentence_id, self.sentence_id))

    async def no_intent(conn, text):
        return False

    async def capture_tts_start(conn, text, sentence_id=None):
        events.append(("tts_start", sentence_id))

    monkeypatch.setattr(receiveAudioHandle, "handle_user_intent", no_intent)
    monkeypatch.setattr(receiveAudioHandle, "send_stt_message", capture_tts_start)

    conn = FakeConnection()
    asyncio.run(receiveAudioHandle.startToChat(conn, "提醒我喝水"))

    start_event = events[0]
    chat_event = events[1]
    assert start_event[0] == "tts_start"
    assert start_event[1] != "previous-sentence"
    assert chat_event == ("chat", start_event[1], start_event[1])
