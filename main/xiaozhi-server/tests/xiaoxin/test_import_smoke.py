import builtins
import importlib
from pathlib import Path
import sys


def test_control_runtime_and_http_server_import_without_opuslib(monkeypatch):
    blocked_name = "opuslib_next"
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == blocked_name:
            raise ModuleNotFoundError("simulated missing opuslib_next")
        return original_import(name, globals, locals, fromlist, level)

    for module_name in (
        "core.http_server",
        "core.api.ota_handler",
        "core.utils.util",
        "core.xiaoxin.control_runtime",
        "core.xiaoxin.runtime",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    monkeypatch.delitem(sys.modules, blocked_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked_import)

    runtime_module = importlib.import_module("core.xiaoxin.runtime")
    control_runtime_module = importlib.import_module("core.xiaoxin.control_runtime")
    http_server_module = importlib.import_module("core.http_server")

    assert runtime_module.XiaoxinRuntime is not None
    assert control_runtime_module.create_xiaoxin_control_runtime is not None
    assert http_server_module.SimpleHttpServer is not None

def test_startup_import_chain_without_opuslib(monkeypatch):
    blocked_name = "opuslib_next"
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == blocked_name:
            raise ModuleNotFoundError("simulated missing opuslib_next")
        return original_import(name, globals, locals, fromlist, level)

    for module_name in (
        "core.providers.vad.silero",
        "core.providers.asr.aliyunbl_stream",
        "core.providers.asr.aliyun_stream",
        "core.providers.asr.doubao_stream",
        "core.providers.asr.xunfei_stream",
        "core.providers.asr.base",
        "core.utils.opus_encoder_utils",
        "core.utils.asr",
        "core.utils.vad",
        "core.utils.modules_initialize",
        "core.connection",
        "core.websocket_server",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    monkeypatch.delitem(sys.modules, blocked_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked_import)

    silero_module = importlib.import_module("core.providers.vad.silero")
    aliyun_stream_module = importlib.import_module("core.providers.asr.aliyun_stream")
    aliyunbl_stream_module = importlib.import_module("core.providers.asr.aliyunbl_stream")
    doubao_stream_module = importlib.import_module("core.providers.asr.doubao_stream")
    xunfei_stream_module = importlib.import_module("core.providers.asr.xunfei_stream")
    websocket_server_module = importlib.import_module("core.websocket_server")

    assert silero_module.VADProvider is not None
    assert aliyun_stream_module.ASRProvider is not None
    assert aliyunbl_stream_module.ASRProvider is not None
    assert doubao_stream_module.ASRProvider is not None
    assert xunfei_stream_module.ASRProvider is not None
    assert websocket_server_module.WebSocketServer is not None


def test_silero_vad_source_avoids_reimporting_opus_in_except_clause():
    silero_module = importlib.import_module("core.providers.vad.silero")
    source = Path(silero_module.__file__).read_text(encoding="utf-8")

    assert "except _get_opuslib_next().OpusError" not in source
    assert "opuslib_next = _get_opuslib_next()" in source
    assert source.index("opuslib_next = _get_opuslib_next()") < source.index(
        "except Exception as e:"
    )
