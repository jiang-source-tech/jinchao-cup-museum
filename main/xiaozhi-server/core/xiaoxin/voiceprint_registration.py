from __future__ import annotations

from dataclasses import dataclass
import hashlib
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientSession, ClientTimeout, FormData


class VoiceprintRegistrationError(RuntimeError):
    """The configured voiceprint provider rejected or could not process audio."""


def voiceprint_speaker_id(owner_user_id: str, pet_id: str) -> str:
    """Return a stable provider-safe ID without exposing local identity IDs."""

    digest = hashlib.sha256(
        f"xiaoxin-voiceprint\x1f{owner_user_id}\x1f{pet_id}".encode("utf-8")
    ).hexdigest()[:32]
    return f"xiaoxin_{digest}"


@dataclass(frozen=True)
class VoiceprintRegistrationResult:
    speaker_id: str
    provider_response: dict[str, object]


class VoiceprintRegistrar:
    """Small server-side adapter for the self-hosted voiceprint-api contract."""

    def __init__(self, config: dict | None):
        self._config = dict(config or {})
        self._health_url = str(self._config.get("url") or "").strip()

    @property
    def configured(self) -> bool:
        parsed = urlparse(self._health_url)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and bool(parse_qs(parsed.query).get("key", [""])[0])
        )

    async def check_health(self) -> bool:
        """Check the configured provider without exposing its credentials."""

        if not self.configured:
            return False
        try:
            async with ClientSession(timeout=ClientTimeout(total=3)) as session:
                async with session.get(self._health_url) as response:
                    if response.status != 200:
                        return False
                    body = await response.json(content_type=None)
                    return isinstance(body, dict) and body.get("status") == "healthy"
        except Exception:
            return False

    async def register(
        self,
        *,
        speaker_id: str,
        audio: bytes,
        filename: str = "voiceprint.wav",
        content_type: str = "audio/wav",
    ) -> VoiceprintRegistrationResult:
        if not self.configured:
            raise VoiceprintRegistrationError("voiceprint service is not configured")
        if not audio:
            raise VoiceprintRegistrationError("voiceprint audio is empty")

        parsed = urlparse(self._health_url)
        token = parse_qs(parsed.query).get("key", [""])[0]
        register_url = f"{parsed.scheme}://{parsed.netloc}/voiceprint/register"
        form = FormData()
        form.add_field("speaker_id", speaker_id)
        form.add_field(
            "file",
            audio,
            filename=filename or "voiceprint.wav",
            content_type=content_type or "audio/wav",
        )
        try:
            async with ClientSession(timeout=ClientTimeout(total=15)) as session:
                async with session.post(
                    register_url,
                    headers={"Authorization": f"Bearer {token}"},
                    data=form,
                ) as response:
                    body = await response.json(content_type=None)
                    if response.status >= 400:
                        raise VoiceprintRegistrationError(
                            f"voiceprint registration failed ({response.status})"
                        )
                    if not isinstance(body, dict):
                        raise VoiceprintRegistrationError(
                            "voiceprint provider returned an invalid response"
                        )
                    # voiceprint-api returns HTTP 200 for an application-level
                    # registration failure, so the JSON success flag is part
                    # of the contract and must be checked explicitly.
                    if body.get("success") is not True:
                        raise VoiceprintRegistrationError(
                            "voiceprint provider rejected the audio"
                        )
        except VoiceprintRegistrationError:
            raise
        except Exception as exc:
            raise VoiceprintRegistrationError(
                "voiceprint provider is unavailable"
            ) from exc
        return VoiceprintRegistrationResult(
            speaker_id=speaker_id,
            provider_response={str(key): value for key, value in body.items()},
        )
