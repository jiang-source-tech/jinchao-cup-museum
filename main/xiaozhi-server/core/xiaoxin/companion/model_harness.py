from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import hashlib
import json
from time import monotonic
from types import MappingProxyType
from typing import Callable, Generic, Mapping, TypeVar

from config.logger import setup_logging


TAG = __name__
LOGGER = setup_logging()
T = TypeVar("T")
_JSON_OBJECT = MappingProxyType({"type": "json_object"})
_REPAIR_INSTRUCTION = (
    "上一条回复未通过结构校验。请只修复 JSON 语法、字段、字段类型和对象形状，"
    "不得增加、删除或改写任何事实、引用、主体、时间或结论。只返回修复后的 JSON。"
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptSpec:
    task_id: str
    semantic_version: str
    system_prompt: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    response_format: Mapping[str, str] = field(default_factory=lambda: _JSON_OBJECT)

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.semantic_version.strip():
            raise ValueError("prompt task and version must be non-empty")
        if not self.system_prompt.strip():
            raise ValueError("prompt system text must be non-empty")
        if self.max_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("prompt limits must be positive")
        object.__setattr__(self, "response_format", MappingProxyType(dict(self.response_format)))

    @property
    def prompt_hash(self) -> str:
        return _digest(
            {
                "task_id": self.task_id,
                "semantic_version": self.semantic_version,
                "system_prompt": self.system_prompt,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "timeout_seconds": self.timeout_seconds,
                "response_format": dict(self.response_format),
                "repair_instruction": _REPAIR_INSTRUCTION,
            }
        )

    @property
    def prompt_version(self) -> str:
        return f"{self.semantic_version}@sha256:{self.prompt_hash}"

    def manifest(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "semantic_version": self.semantic_version,
            "prompt_hash": self.prompt_hash,
            "prompt_version": self.prompt_version,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "response_format": dict(self.response_format),
        }


class StructuredOutputError(ValueError):
    """A response-shape error eligible for one structure-only repair."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StructuredCompletion(Generic[T]):
    value: T
    prompt_version: str
    prompt_hash: str
    repair_count: int
    request_digest: str
    response_digest: str
    duration_ms: float


class StructuredJsonHarness:
    """Run one narrow JSON model task without owning domain state or persistence."""

    def __init__(
        self,
        adapter: object,
        *,
        audit_sink: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._audit_sink = audit_sink

    @property
    def model_name(self) -> str:
        provider = getattr(self._adapter, "llm", self._adapter)
        value = getattr(provider, "model_name", None)
        return value if isinstance(value, str) and value else type(provider).__name__

    def complete(
        self,
        *,
        spec: PromptSpec,
        user_payload: object,
        parser: Callable[[object], T],
        validator: Callable[[T], T] | None = None,
        correlation: Mapping[str, object] | None = None,
    ) -> StructuredCompletion[T]:
        messages = [
            {"role": "system", "content": spec.system_prompt},
            {
                "role": "user",
                "content": _canonical_json(user_payload),
            },
        ]
        request_digest = _digest(messages)
        started_at = monotonic()
        repair_count = 0
        error_code = None
        raw: object = None

        def parse_and_validate(candidate: object) -> T:
            value = parser(candidate)
            return validator(value) if validator is not None else value

        try:
            raw = self._call(
                spec,
                messages,
                temperature=spec.temperature,
                attempt="initial",
                correlation=correlation,
            )
            try:
                value = parse_and_validate(raw)
            except StructuredOutputError as exc:
                error_code = exc.code
                repair_count = 1
                repair_messages = [
                    *messages,
                    {
                        "role": "assistant",
                        "content": raw if isinstance(raw, str) else _canonical_json(raw),
                    },
                    {
                        "role": "user",
                        "content": _canonical_json(
                            {
                                "instruction": _REPAIR_INSTRUCTION,
                                "validation_error": exc.code,
                                "validation_message": str(exc),
                            }
                        ),
                    },
                ]
                raw = self._call(
                    spec,
                    repair_messages,
                    temperature=0.0,
                    attempt="repair",
                    correlation=correlation,
                )
                value = parse_and_validate(raw)
            duration_ms = max((monotonic() - started_at) * 1000, 0.0)
            result = StructuredCompletion(
                value=value,
                prompt_version=spec.prompt_version,
                prompt_hash=spec.prompt_hash,
                repair_count=repair_count,
                request_digest=request_digest,
                response_digest=_digest(raw),
                duration_ms=duration_ms,
            )
            self._audit(
                spec=spec,
                outcome="succeeded",
                duration_ms=duration_ms,
                repair_count=repair_count,
                request_digest=request_digest,
                response_digest=result.response_digest,
                error_code=error_code,
                correlation=correlation,
                attempt="final_validation",
            )
            return result
        except Exception as exc:
            duration_ms = max((monotonic() - started_at) * 1000, 0.0)
            self._audit(
                spec=spec,
                outcome="failed",
                duration_ms=duration_ms,
                repair_count=repair_count,
                request_digest=request_digest,
                response_digest=_digest(raw) if raw is not None else None,
                error_code=getattr(exc, "code", None) or type(exc).__name__,
                correlation=correlation,
                attempt="final_validation",
            )
            raise

    def _call(
        self,
        spec: PromptSpec,
        messages: list[dict[str, object]],
        *,
        temperature: float,
        attempt: str,
        correlation: Mapping[str, object] | None,
    ) -> object:
        request_digest = _digest(messages)
        started_at = monotonic()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            self._adapter.complete_chat,
            messages,
            max_tokens=spec.max_tokens,
            temperature=temperature,
            response_format=dict(spec.response_format),
        )
        try:
            raw = future.result(timeout=spec.timeout_seconds)
            self._audit(
                spec=spec,
                outcome="model_completed",
                duration_ms=max((monotonic() - started_at) * 1000, 0.0),
                repair_count=1 if attempt == "repair" else 0,
                request_digest=request_digest,
                response_digest=_digest(raw),
                error_code=None,
                correlation=correlation,
                attempt=attempt,
            )
            return raw
        except FutureTimeoutError as exc:
            future.cancel()
            self._audit(
                spec=spec,
                outcome="model_failed",
                duration_ms=max((monotonic() - started_at) * 1000, 0.0),
                repair_count=1 if attempt == "repair" else 0,
                request_digest=request_digest,
                response_digest=None,
                error_code="model_timeout",
                correlation=correlation,
                attempt=attempt,
            )
            raise TimeoutError(f"{spec.task_id} model timed out") from exc
        except Exception as exc:
            self._audit(
                spec=spec,
                outcome="model_failed",
                duration_ms=max((monotonic() - started_at) * 1000, 0.0),
                repair_count=1 if attempt == "repair" else 0,
                request_digest=request_digest,
                response_digest=None,
                error_code=type(exc).__name__,
                correlation=correlation,
                attempt=attempt,
            )
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _audit(
        self,
        *,
        spec: PromptSpec,
        outcome: str,
        duration_ms: float,
        repair_count: int,
        request_digest: str,
        response_digest: str | None,
        error_code: str | None,
        correlation: Mapping[str, object] | None,
        attempt: str,
    ) -> None:
        payload = {
            "event": "xiaoxin_model_invocation",
            "task_id": spec.task_id,
            "model": self.model_name,
            "prompt_version": spec.prompt_version,
            "prompt_hash": spec.prompt_hash,
            "repair_count": repair_count,
            "attempt": attempt,
            "duration_ms": round(duration_ms, 3),
            "outcome": outcome,
            "error_code": error_code,
            "request_digest": request_digest,
            "response_digest": response_digest,
            "correlation": dict(correlation or {}),
        }
        if self._audit_sink is not None:
            self._audit_sink(payload)
        LOGGER.bind(tag=TAG).info(_canonical_json(payload))
