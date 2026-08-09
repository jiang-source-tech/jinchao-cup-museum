#!/usr/bin/env python3
"""Regenerate static speech prompts with the configured Qwen Realtime TTS voice.

The script reads the same merged local configuration as the server, generates every
speech asset into a temporary directory, validates the returned PCM, and only then
replaces the checked-in WAV files. API keys are never accepted on the command line
or printed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
import wave
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
import yaml


SERVER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SERVER_DIR / "config.yaml"
LOCAL_CONFIG = SERVER_DIR / "data" / ".config.yaml"
ASSETS_DIR = SERVER_DIR / "config" / "assets"

PRESET_TEXTS = {
    Path("bind_code.wav"): "请登录控制面板，输入",
    **{Path("bind_code") / f"{digit}.wav": spoken for digit, spoken in zip(
        "0123456789", "零一二三四五六七八九", strict=True
    )},
    Path("bind_not_found.wav"): "没有找到该设备的版本信息，请正确配置OTA地址，然后重新编译固件。",
    Path("max_output_size.wav"): "不好意思，我现在有点事情要忙，明天这个时候我们再聊，约好了哦！明天不见不散，拜拜！",
    Path("wakeup_words_short.wav"): "我在这里哦！",
}


def merge_configs(base: object, override: object) -> object:
    """Recursively merge mappings with override values taking precedence."""
    if not isinstance(base, Mapping) or not isinstance(override, Mapping):
        return override
    merged = dict(base)
    for key, value in override.items():
        if key in merged:
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_tts_config() -> tuple[str, dict]:
    with DEFAULT_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if LOCAL_CONFIG.exists():
        with LOCAL_CONFIG.open("r", encoding="utf-8") as file:
            config = merge_configs(config, yaml.safe_load(file) or {})

    selected = config.get("selected_module", {}).get("TTS")
    provider = config.get("TTS", {}).get(selected, {})
    if not selected or not isinstance(provider, dict):
        raise RuntimeError("No selected TTS provider was found in the merged config")
    if provider.get("type") != "qwen_realtime":
        raise RuntimeError(
            f"Selected TTS provider {selected!r} has unsupported type "
            f"{provider.get('type')!r}; this generator currently requires qwen_realtime"
        )
    if not provider.get("api_key"):
        raise RuntimeError(f"Selected TTS provider {selected!r} has no api_key")
    return selected, provider


def build_ws_url(config: dict) -> str:
    model = config.get("model", "qwen3-tts-instruct-flash-realtime")
    url = config.get("url") or config.get("ws_url")
    if not url:
        workspace_id = config.get("workspace_id")
        if workspace_id:
            region = config.get("region", "cn-beijing")
            url = f"wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/realtime"
        else:
            url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("model", model)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def build_session_config(config: dict) -> dict:
    session = {
        "mode": config.get("mode", "server_commit"),
        "voice": config.get("private_voice") or config.get("voice", "Cherry"),
        "language_type": config.get("language_type", "Auto"),
        "response_format": "pcm",
        "sample_rate": int(config.get("sample_rate", 24000)),
        "volume": int(config.get("volume", 50)),
    }
    if config.get("instructions"):
        session["instructions"] = config["instructions"]
    optimize = optional_bool(config.get("optimize_instructions"))
    if optimize is not None:
        session["optimize_instructions"] = optimize
    return session


async def synthesize(text: str, config: dict) -> bytes:
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    timeout = float(config.get("preset_generation_timeout", 60))
    chunks: list[bytes] = []

    async with websockets.connect(
        build_ws_url(config),
        additional_headers=headers,
        ping_interval=30,
        ping_timeout=10,
        close_timeout=10,
        max_size=10 * 1024 * 1024,
    ) as websocket:
        async def send(event: dict) -> None:
            event["event_id"] = (
                f"event_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            )
            await websocket.send(json.dumps(event, ensure_ascii=False))

        await send({"type": "session.update", "session": build_session_config(config)})
        await send({"type": "input_text_buffer.append", "text": text})
        await send({"type": "session.finish"})

        async with asyncio.timeout(timeout):
            while True:
                message = await websocket.recv()
                if not isinstance(message, str):
                    continue
                event = json.loads(message)
                event_type = event.get("type")
                if event_type == "response.audio.delta":
                    delta = event.get("delta")
                    if delta:
                        chunks.append(base64.b64decode(delta, validate=True))
                elif event_type == "error":
                    error = event.get("error") or event
                    code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
                    message_text = (
                        error.get("message", str(error))
                        if isinstance(error, dict)
                        else str(error)
                    )
                    raise RuntimeError(f"TTS API error {code}: {message_text}")
                elif event_type == "session.finished":
                    break

    pcm = b"".join(chunks)
    if len(pcm) < 2_000:
        raise RuntimeError(
            f"TTS API returned only {len(pcm)} PCM bytes for text {text!r}"
        )
    if len(pcm) % 2:
        raise RuntimeError(f"TTS API returned an odd PCM byte count for text {text!r}")
    return pcm


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
    with wave.open(str(path), "rb") as generated:
        duration = generated.getnframes() / generated.getframerate()
        if (
            generated.getnchannels() != 1
            or generated.getsampwidth() != 2
            or generated.getframerate() != sample_rate
            or duration < 0.08
        ):
            raise RuntimeError(f"Generated WAV validation failed: {path}")
    return duration


async def generate_all(config: dict, staging_dir: Path) -> list[tuple[Path, float]]:
    sample_rate = int(config.get("sample_rate", 24000))
    generated = []
    for relative_path, prompt in PRESET_TEXTS.items():
        pcm = await synthesize(prompt, config)
        duration = len(pcm) / (sample_rate * 2)
        if relative_path.parent == Path("bind_code"):
            # A one-character prompt can occasionally produce an expressive,
            # unnaturally long reading. Retry and retain the shortest valid take.
            attempts = [pcm]
            for _ in range(2):
                if duration <= 1.5:
                    break
                retry = await synthesize(prompt, config)
                attempts.append(retry)
                pcm = min(attempts, key=len)
                duration = len(pcm) / (sample_rate * 2)
            if duration > 1.5:
                raise RuntimeError(
                    f"Digit preset {relative_path} is unexpectedly long: {duration:.2f}s"
                )
        duration = write_wav(staging_dir / relative_path, pcm, sample_rate)
        generated.append((relative_path, duration))
        print(f"generated {relative_path.as_posix():32} {duration:6.2f}s")
    return generated


def replace_assets(staging_dir: Path) -> None:
    backup_dir = Path(
        tempfile.mkdtemp(prefix=".xiaoxin-preset-backup-", dir=ASSETS_DIR.parent)
    )
    replaced: list[Path] = []
    try:
        for relative_path in PRESET_TEXTS:
            destination = ASSETS_DIR / relative_path
            backup = backup_dir / relative_path
            backup.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.copy2(destination, backup)
            os.replace(staging_dir / relative_path, destination)
            replaced.append(relative_path)
    except Exception:
        for relative_path in replaced:
            backup = backup_dir / relative_path
            destination = ASSETS_DIR / relative_path
            if backup.exists():
                os.replace(backup, destination)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="generate and validate all files without replacing repository assets",
    )
    args = parser.parse_args()

    selected, config = load_tts_config()
    voice = config.get("private_voice") or config.get("voice", "Cherry")
    print(
        f"provider={selected} model={config.get('model')} voice={voice} "
        f"sample_rate={int(config.get('sample_rate', 24000))}"
    )

    # Keep staging on the same volume as the destination so os.replace remains
    # atomic on Windows as well as POSIX systems.
    with tempfile.TemporaryDirectory(
        prefix=".xiaoxin-preset-audio-", dir=ASSETS_DIR.parent
    ) as temp:
        staging_dir = Path(temp)
        await generate_all(config, staging_dir)
        if args.check_only:
            print("check-only complete; repository assets were not changed")
        else:
            replace_assets(staging_dir)
            print(f"replaced {len(PRESET_TEXTS)} preset speech assets")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
