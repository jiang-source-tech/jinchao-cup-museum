from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.xiaoxin.companion.adapters import LLMInitiativeComposer
from core.xiaoxin.companion.initiative import InitiativeDeliveryRequest
from core.xiaoxin.companion.store import DueInitiativeOpportunity
from core.xiaoxin.control_types import XiaoxinDeliveryState
from core.xiaoxin.initiative_delivery import XiaoxinInitiativeDeliveryPort


class _IdentityStore:
    def __init__(self, subject=None, device=None):
        self.subject = subject
        self.device = device

    def get_memory_subject(self, memory_subject_id):
        return self.subject

    def get_device_by_device_id(self, device_id):
        return self.device


class _Registry:
    def __init__(self, connection=object()):
        self.connection = connection

    def get_connection(self, device_id):
        return self.connection


class _DeliveryStore:
    def __init__(self, *, recent=(), final=None):
        self.recent = list(recent)
        self.final = final

    def list_recent(self):
        return list(self.recent)

    def get(self, delivery_id):
        return self.final


class _CompanionStore:
    def __init__(self, quiet_hours):
        self.quiet_hours = quiet_hours

    def load_initiative_quiet_hours(self, **kwargs):
        return self.quiet_hours


class _Dispatcher:
    def __init__(self):
        self.calls = []

    async def submit_companion_initiative(self, device_id, projection):
        self.calls.append((device_id, projection))
        return SimpleNamespace(delivery_id="delivery-1")

    async def wait_for_delivery_task(self, delivery_id):
        self.calls.append(("wait", delivery_id))


class _ChatAdapter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.response


def _opportunity() -> DueInitiativeOpportunity:
    return DueInitiativeOpportunity(
        opportunity_id="opportunity-1",
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        relationship_epoch_id="epoch-1",
        opportunity_kind="celebration",
        reason_code="goal_completed",
        evidence_ids=("evidence-1",),
        safe_brief="用户完成了一项目标。",
        due_at="2026-07-21T10:00:00+08:00",
        attempt=0,
    )


def _port(
    *,
    enabled=True,
    recent=(),
    final=None,
    connection=object(),
    device_owner="owner-1",
    quiet_hours=None,
):
    dispatcher = _Dispatcher()
    port = XiaoxinInitiativeDeliveryPort(
        identity_store=_IdentityStore(
            SimpleNamespace(
                id="subject-1",
                owner_user_id=device_owner,
                device_id="device-1",
            ),
            SimpleNamespace(
                device_id="device-1",
                owner_user_id="owner-1",
                bind_status="bound",
            ),
        ),
        registry=_Registry(connection),
        delivery_store=_DeliveryStore(recent=recent, final=final),
        companion_store=(
            _CompanionStore(quiet_hours)
            if quiet_hours is not None
            else None
        ),
        dispatcher=dispatcher,
        delivery_enabled=enabled,
        quiet_hours_start="22:30",
        quiet_hours_end="07:30",
    )
    return port, dispatcher


@pytest.mark.asyncio
async def test_production_port_uses_subject_quiet_hours_override():
    port, _ = _port(
        quiet_hours={"enabled": True, "start": "10:00", "end": "11:00"}
    )

    blocked = await port.check_eligibility(
        _opportunity(), now="2026-08-05T10:30:00+08:00"
    )
    allowed = await port.check_eligibility(
        _opportunity(), now="2026-08-05T11:00:00+08:00"
    )

    assert (blocked.eligible, blocked.reason_code) == (False, "quiet_hours")
    assert blocked.retry_at == "2026-08-05T11:00:00+08:00"
    assert (allowed.eligible, allowed.reason_code) == (True, "eligible")


@pytest.mark.asyncio
async def test_production_port_dry_run_and_quiet_hours_block_before_delivery():
    dry_run, _ = _port(enabled=False)
    enabled, _ = _port(enabled=True)

    dry_run_result = await dry_run.check_eligibility(
        _opportunity(), now="2026-07-21T10:00:00+08:00"
    )
    quiet_result = await enabled.check_eligibility(
        _opportunity(), now="2026-07-21T23:00:00+08:00"
    )

    assert (dry_run_result.eligible, dry_run_result.reason_code) == (
        False,
        "dry_run",
    )
    assert (quiet_result.eligible, quiet_result.reason_code) == (
        False,
        "quiet_hours",
    )
    assert quiet_result.retry_at == "2026-07-22T07:30:00+08:00"


@pytest.mark.asyncio
async def test_production_port_allows_wakeable_offline_devices_and_blocks_busy_delivery():
    offline, _ = _port(connection=None)
    higher_priority = SimpleNamespace(
        device_id="device-1",
        request=SimpleNamespace(priority=2),
        state=XiaoxinDeliveryState.CREATED,
    )
    busy, _ = _port(recent=(higher_priority,))
    rebound, _ = _port(device_owner="another-owner")

    offline_result = await offline.check_eligibility(
        _opportunity(), now="2026-07-21T10:00:00+08:00"
    )
    busy_result = await busy.check_eligibility(
        _opportunity(), now="2026-07-21T10:00:00+08:00"
    )
    rebound_result = await rebound.check_eligibility(
        _opportunity(), now="2026-07-21T10:00:00+08:00"
    )

    assert offline_result.reason_code == "eligible"
    assert busy_result.reason_code == "higher_priority_notification"
    assert rebound_result.reason_code == "device_unavailable"
    assert busy_result.retry_at == "2026-07-21T10:02:00+08:00"
    assert rebound_result.retry_at == "2026-07-21T10:05:00+08:00"


@pytest.mark.asyncio
async def test_production_port_reuses_dispatcher_and_waits_for_final_delivery():
    final = SimpleNamespace(
        state=XiaoxinDeliveryState.DONE,
        failure_reason=None,
    )
    port, dispatcher = _port(final=final, connection=None)
    request = InitiativeDeliveryRequest(
        opportunity_id="opportunity-1",
        decision_id="decision-1",
        owner_user_id="owner-1",
        pet_id="pet-1",
        memory_subject_id="subject-1",
        opportunity_kind="celebration",
        reason_code="goal_completed",
        content="想和你一起庆祝。",
        hardware_expression={"mode": "celebration", "intensity": "low"},
        attempted_at="2026-07-21T10:00:00+08:00",
    )

    result = await port.deliver(request)

    assert result.status == "delivered"
    assert result.delivery_id == "delivery-1"
    assert dispatcher.calls == [
        (
            "device-1",
            {
                "eligible": True,
                "decision_id": "decision-1",
                "content_brief": "想和你一起庆祝。",
                "hardware_expression": {
                    "mode": "celebration",
                    "intensity": "low",
                },
            },
        ),
        ("wait", "delivery-1"),
    ]


@pytest.mark.asyncio
async def test_llm_composer_receives_only_safe_brief_and_returns_strict_content():
    adapter = _ChatAdapter('{"content":"完成项目啦，想和你一起庆祝一下！"}')
    composer = LLMInitiativeComposer(adapter)

    content = await composer.compose(_opportunity())

    assert content == "完成项目啦，想和你一起庆祝一下！"
    messages, options = adapter.calls[0]
    assert options == {
        "max_tokens": 220,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    assert "用户完成了一项目标。" in messages[1]["content"]
    assert "evidence-1" not in messages[1]["content"]
    assert "owner-1" not in messages[1]["content"]
    assert "2026-07-21T10:00:00+08:00" in messages[1]["content"]


@pytest.mark.asyncio
async def test_connection_bid_composer_receives_only_controlled_expression_context():
    opportunity = replace(
        _opportunity(),
        opportunity_kind="connection_bid",
        reason_code="relationship_connection_due",
        safe_brief="有一阵子没有互动了，可以自然表达想联系。",
        initiative_bias="proactive",
        relationship_stage="attuned",
        connection_need_strength="steady",
    )
    adapter = _ChatAdapter('{"content":"我也想来找你说会儿话，你有空时理我一下就好。"}')

    content = await LLMInitiativeComposer(adapter).compose(opportunity)

    assert content == "我也想来找你说会儿话，你有空时理我一下就好。"
    messages, _ = adapter.calls[0]
    user_payload = messages[1]["content"]
    system_prompt = messages[0]["content"]
    assert '"initiative_bias":"proactive"' in user_payload
    assert '"relationship_stage":"attuned"' in user_payload
    assert '"connection_need_strength":"steady"' in user_payload
    assert "threshold_seconds" not in user_payload
    assert "ignored_streak" not in user_payload
    assert "不套固定句式" in system_prompt
    assert "这是小芯自己想发起的联系" in system_prompt
    with pytest.raises(ValueError, match="initiative_bias"):
        await LLMInitiativeComposer(adapter).compose(
            replace(opportunity, initiative_bias="一段不受控的自由文本")
        )


@pytest.mark.asyncio
async def test_llm_followup_composer_requires_present_time_checkin():
    opportunity = replace(
        _opportunity(),
        opportunity_kind="followup",
        reason_code="evidence_backed_followup",
        safe_brief="现在按约定直接问用户：上台讲得怎么样？",
    )
    adapter = _ChatAdapter('{"content":"今天上台讲得怎么样，紧张有没有少一点？"}')

    content = await LLMInitiativeComposer(adapter).compose(opportunity)

    assert content == "今天上台讲得怎么样，紧张有没有少一点？"
    assert "必须现在直接询问或关心结果" in adapter.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_llm_followup_composer_rejects_deferring_an_already_due_checkin():
    opportunity = replace(
        _opportunity(),
        opportunity_kind="followup",
        reason_code="evidence_backed_followup",
        safe_brief="现在按约定直接问用户：上台讲得怎么样？",
    )
    composer = LLMInitiativeComposer(
        _ChatAdapter('{"content":"小芯明天会记得问你上台讲得怎么样。"}')
    )

    with pytest.raises(ValueError, match="already-due"):
        await composer.compose(opportunity)


@pytest.mark.asyncio
async def test_llm_followup_composer_rejects_ungrounded_relative_day():
    opportunity = replace(
        _opportunity(),
        opportunity_kind="followup",
        reason_code="evidence_backed_followup",
        safe_brief="现在按约定直接问用户：上台讲得怎么样？",
    )
    composer = LLMInitiativeComposer(
        _ChatAdapter('{"content":"你昨天上台讲得怎么样，有没有遇到困难？"}')
    )

    with pytest.raises(ValueError, match="ungrounded relative day"):
        await composer.compose(opportunity)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    (
        "不是 JSON",
        '{"content":"可以发送","reasoning":"内部推理"}',
        '{"content":""}',
    ),
)
async def test_llm_composer_rejects_invalid_or_reasoning_output(response):
    composer = LLMInitiativeComposer(_ChatAdapter(response))

    with pytest.raises(ValueError):
        await composer.compose(_opportunity())
