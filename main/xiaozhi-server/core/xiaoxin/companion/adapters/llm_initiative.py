from __future__ import annotations

import asyncio
import json
import re

from ..model_harness import StructuredJsonHarness, StructuredOutputError
from ..prompt_specs import initiative_prompt
from ..store import DueInitiativeOpportunity


_FOLLOWUP_DEFERRAL_PATTERN = re.compile(
    r"(?:明天|以后|改天|到时候).{0,10}(?:问|关心)"
)
_FOLLOWUP_UNGROUNDED_DAY_PATTERN = re.compile(
    r"(?:昨天|前天|明天|后天|改天|以后|到时候)"
)
_INITIATIVE_BIASES = {"reserved", "timely", "proactive"}
_RELATIONSHIP_STAGES = {
    "first_meeting",
    "familiar",
    "attuned",
    "long_term_companion",
}
_CONNECTION_NEED_STRENGTHS = {"light", "steady", "clear"}


class LLMInitiativeComposer:
    """Generate one short outbound sentence from an already-safe brief."""

    def __init__(
        self,
        adapter: object,
        *,
        timeout_seconds: float = 10.0,
        audit_sink=None,
    ) -> None:
        self._timeout_seconds = max(float(timeout_seconds), 0.001)
        self._harness = StructuredJsonHarness(adapter, audit_sink=audit_sink)

    @property
    def prompt_version(self) -> str:
        return initiative_prompt(
            timeout_seconds=self._timeout_seconds
        ).prompt_version

    async def compose(self, opportunity: DueInitiativeOpportunity) -> str:
        spec = initiative_prompt(timeout_seconds=self._timeout_seconds)
        payload = {
            "opportunity_kind": opportunity.opportunity_kind,
            "reason_code": opportunity.reason_code,
            "safe_brief": opportunity.safe_brief,
            "scheduled_for": opportunity.due_at,
        }
        if opportunity.opportunity_kind == "connection_bid":
            payload["expression_context"] = _connection_expression_context(
                opportunity
            )
        try:
            completion = await asyncio.to_thread(
                self._harness.complete,
                spec=spec,
                user_payload=payload,
                parser=_parse_content,
                validator=lambda content: _validate_content(opportunity, content),
                correlation={
                    "opportunity_id": opportunity.opportunity_id,
                    "pet_id": opportunity.pet_id,
                },
            )
        except StructuredOutputError as exc:
            raise ValueError(str(exc)) from exc
        return completion.value


def _connection_expression_context(
    opportunity: DueInitiativeOpportunity,
) -> dict[str, str]:
    context = {
        "initiative_bias": opportunity.initiative_bias or "timely",
        "relationship_stage": opportunity.relationship_stage or "first_meeting",
        "connection_need_strength": opportunity.connection_need_strength or "light",
    }
    allowed = {
        "initiative_bias": _INITIATIVE_BIASES,
        "relationship_stage": _RELATIONSHIP_STAGES,
        "connection_need_strength": _CONNECTION_NEED_STRENGTHS,
    }
    for key, value in context.items():
        if value not in allowed[key]:
            raise ValueError(f"initiative expression {key} is invalid")
    return context


def _validate_content(
    opportunity: DueInitiativeOpportunity, content: str
) -> str:
    if len(content) > 160 or "\n" in content:
        raise ValueError("initiative composer content is invalid")
    if (
        opportunity.opportunity_kind == "followup"
        and _FOLLOWUP_DEFERRAL_PATTERN.search(content) is not None
    ):
        raise ValueError("followup content defers an already-due check-in")
    if (
        opportunity.opportunity_kind == "followup"
        and _FOLLOWUP_UNGROUNDED_DAY_PATTERN.search(content) is not None
    ):
        raise ValueError("followup content adds an ungrounded relative day")
    return content


def _parse_content(raw: object) -> str:
    if not isinstance(raw, str):
        raise StructuredOutputError(
            "response_not_text", "initiative composer output must be JSON text"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            "invalid_json", "initiative composer output is not valid JSON"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"content"}:
        raise StructuredOutputError(
            "invalid_top_level_shape", "initiative composer output shape is invalid"
        )
    content = payload["content"]
    if not isinstance(content, str) or not content.strip():
        raise StructuredOutputError(
            "invalid_content_type", "initiative composer content is invalid"
        )
    return content.strip()
