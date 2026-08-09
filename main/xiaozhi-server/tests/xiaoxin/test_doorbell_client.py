import json

from core.xiaoxin.doorbell_client import DoorbellMqttSettings, XiaoxinDoorbellClient
from core.xiaoxin.registry import XiaoxinDeviceRegistry
from core.xiaoxin.tenant_config import load_tenant_config


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakePahoClient:
    def __init__(self, publish_rc=0):
        self.on_connect = None
        self.on_message = None
        self.on_publish = None
        self.connected = False
        self.subscriptions = []
        self.published = []
        self.username_password = None
        self.publish_rc = publish_rc
        self.next_mid = 1

    def username_pw_set(self, username=None, password=None):
        self.username_password = (username, password)

    def will_set(self, *args, **kwargs):
        pass

    def connect(self, host, port, keepalive):
        self.connected = True
        self.host = host
        self.port = port
        self.keepalive = keepalive

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        result = type(
            "Result",
            (),
            {"rc": self.publish_rc, "mid": self.next_mid},
        )()
        self.next_mid += 1
        return result

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        self.connected = False


class RefusingPahoClient(FakePahoClient):
    def connect(self, host, port, keepalive):
        raise ConnectionRefusedError("broker refused")


class FakeRunningLoop:
    def __init__(self):
        self.scheduled = []

    def is_running(self):
        return True

    def call_soon_threadsafe(self, callback, *args):
        self.scheduled.append((callback, args))

    def run_scheduled(self):
        while self.scheduled:
            callback, args = self.scheduled.pop(0)
            callback(*args)


class ClosingRuntimeLoop:
    def __init__(self):
        self.schedule_attempts = 0

    def is_running(self):
        return True

    def call_soon_threadsafe(self, callback, *args):
        self.schedule_attempts += 1
        raise RuntimeError("event loop is closed")


class FailedReasonCode:
    is_failure = True


class SuccessfulReasonCode:
    is_failure = False


def started_client(*, fake=None, loop=None):
    registry = XiaoxinDeviceRegistry()
    fake = fake or FakePahoClient()
    tenant = load_tenant_config(
        {
            "xiaoxin_control": {
                "doorbell_mqtt": {
                    "endpoint": "localhost:1883",
                    "qos": 1,
                }
            }
        }
    )
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        registry,
        tenant=tenant,
        client_factory=lambda: fake,
    )
    if loop is None:
        client.start_without_loop_for_test()
    else:
        client.start(loop)
    return client, fake


def test_settings_prefers_xiaoxin_control_endpoint_over_server_gateway():
    settings = DoorbellMqttSettings.from_config(
        {
            "server": {"mqtt_gateway": "server.example:1883"},
            "xiaoxin_control": {
                "doorbell_mqtt": {
                    "endpoint": "doorbell.example:1884",
                    "username": "u",
                    "password": "p",
                }
            },
        }
    )

    assert settings.host == "doorbell.example"
    assert settings.port == 1884
    assert settings.username == "u"
    assert settings.password == "p"


def test_settings_prefers_server_endpoint_for_runtime_connection():
    settings = DoorbellMqttSettings.from_config(
        {
            "server": {"mqtt_gateway": "server.example:1883"},
            "xiaoxin_control": {
                "doorbell_mqtt": {
                    "endpoint": "public.example:1883",
                    "server_endpoint": "xiaoxin-doorbell-mqtt:1884",
                }
            },
        }
    )

    assert settings.host == "xiaoxin-doorbell-mqtt"
    assert settings.port == 1884


def test_can_attempt_wake_depends_on_configured_endpoint_not_runtime_status():
    disabled = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host=""),
        XiaoxinDeviceRegistry(),
    )
    enabled = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        XiaoxinDeviceRegistry(),
    )

    assert disabled.can_attempt_wake() is False
    assert enabled.can_attempt_wake() is True


def test_status_message_updates_registry_from_device_status_topic():
    registry = XiaoxinDeviceRegistry()
    fake = FakePahoClient()
    tenant = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "localhost:1883"}}}
    )
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        registry,
        tenant=tenant,
        client_factory=lambda: fake,
    )
    client.start_without_loop_for_test()

    fake.on_message(fake, None, FakeMessage("device/aa:bb/status", b"online"))

    assert registry.list_devices()[0]["device_id"] == "aa:bb"
    assert registry.list_devices()[0]["tenant_id"] == "hzcu-iee"
    assert registry.list_devices()[0]["state"] == "wakeable"


def test_connect_subscribes_to_status_and_telemetry_topics():
    client, fake = started_client()

    fake.on_connect(fake, None, None, SuccessfulReasonCode())

    assert ("device/+/status", 1) in fake.subscriptions
    assert ("device/+/telemetry", 1) in fake.subscriptions


def test_telemetry_message_updates_registry_without_websocket_hello():
    registry = XiaoxinDeviceRegistry()
    fake = FakePahoClient()
    tenant = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "localhost:1883"}}}
    )
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        registry,
        tenant=tenant,
        client_factory=lambda: fake,
    )
    client.start_without_loop_for_test()

    fake.on_message(
        fake,
        None,
        FakeMessage(
            "device/aa:bb/telemetry",
            json.dumps(
                {
                    "battery_level": 3,
                    "battery_percent": 58,
                    "firmware_version": "0.1.3",
                }
            ).encode("utf-8"),
        ),
    )

    device = registry.list_devices()[0]
    assert device["device_id"] == "aa:bb"
    assert device["battery_level"] == 3
    assert device["battery_percent"] == 58
    assert device["firmware_version"] == "0.1.3"
    assert device["last_seen_at"] is not None


def test_publish_wake_uses_device_notification_topic_and_protocol_payload():
    registry = XiaoxinDeviceRegistry()
    fake = FakePahoClient()
    tenant = load_tenant_config(
        {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "localhost:1883"}}}
    )
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        registry,
        tenant=tenant,
        client_factory=lambda: fake,
    )
    client.start_without_loop_for_test()

    assert client.publish_wake("aa:bb") is True

    topic, payload, qos, retain = fake.published[-1]
    assert topic == "device/aa:bb/notification"
    assert json.loads(payload) == {"type": "wake"}
    assert qos == 1
    assert retain is False


def test_publish_overview_is_qos1_retained_utf8_json():
    client, fake = started_client()

    mid = client.publish_overview(
        "aa:bb",
        {
            "type": "xiaoxin_overview_update",
            "revision": 7,
            "summary": "杭州晴",
        },
    )

    assert mid == 1
    topic, payload, qos, retain = fake.published[-1]
    assert topic == "device/aa:bb/overview"
    assert json.loads(payload) == {
        "type": "xiaoxin_overview_update",
        "revision": 7,
        "summary": "杭州晴",
    }
    assert "杭州晴" in payload
    assert payload.encode("utf-8").decode("utf-8") == payload
    assert (qos, retain) == (1, True)


def test_publish_overview_returns_none_when_paho_rejects_publish():
    client, fake = started_client(fake=FakePahoClient(publish_rc=2))

    assert client.publish_overview("aa:bb", {"revision": 8}) is None
    assert fake.published[-1][0] == "device/aa:bb/overview"


def test_publish_overview_in_session_never_rebuilds_or_crosses_generation():
    client, fake = started_client()
    generation = client.publish_session_generation

    assert client.publish_overview_in_session(
        generation + 1,
        "aa:bb",
        {"revision": 8},
    ) is None
    assert fake.published == []
    assert client.publish_overview_in_session(
        generation,
        "aa:bb",
        {"revision": 8},
    ) == 1

    client.stop()

    assert client.publish_overview_in_session(
        generation,
        "aa:bb",
        {"revision": 9},
    ) is None
    assert len(fake.published) == 1


def test_ensure_publish_session_recovers_after_initial_connect_failure():
    recovering = FakePahoClient()
    attempts = iter((RefusingPahoClient(), recovering))
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        XiaoxinDeviceRegistry(),
        tenant=load_tenant_config(
            {
                "xiaoxin_control": {
                    "doorbell_mqtt": {"endpoint": "localhost:1883"}
                }
            }
        ),
        client_factory=lambda: next(attempts),
    )

    client.start_without_loop_for_test()

    assert client.publish_session_generation is None
    recovered_generation = client.ensure_publish_session()
    assert recovered_generation is not None
    assert recovered_generation == client.publish_session_generation


def test_connect_listener_preserves_device_status_subscription_and_isolates_errors():
    client, fake = started_client()
    calls = []

    def broken_listener():
        raise RuntimeError("listener failed")

    client.add_connect_listener(broken_listener)
    client.add_connect_listener(lambda: calls.append("connected"))

    fake.on_connect(fake, None, None, 0, None)

    assert fake.subscriptions == [
        ("device/+/status", 1),
        ("device/+/telemetry", 1),
    ]
    assert calls == ["connected"]


def test_connect_listener_is_not_called_when_broker_rejects_connection():
    client, fake = started_client()
    calls = []
    client.add_connect_listener(lambda: calls.append("connected"))

    fake.on_connect(fake, None, None, FailedReasonCode(), None)

    assert fake.subscriptions == [
        ("device/+/status", 1),
        ("device/+/telemetry", 1),
    ]
    assert calls == []


def test_version2_publish_ack_forwards_mid_and_isolates_listener_errors():
    client, fake = started_client()
    calls = []

    def broken_listener(mid, generation):
        raise RuntimeError(f"listener failed for {mid}/{generation}")

    client.add_publish_ack_listener(broken_listener)
    client.add_publish_ack_listener(
        lambda mid, generation: calls.append((mid, generation))
    )

    fake.on_publish(fake, None, 41, None, None)

    assert calls == [(41, client.publish_session_generation)]


def test_connect_and_publish_ack_listeners_are_marshaled_to_running_runtime_loop():
    loop = FakeRunningLoop()
    client, fake = started_client(loop=loop)
    calls = []
    client.add_connect_listener(lambda: calls.append(("connect", None)))
    client.add_publish_ack_listener(
        lambda mid, generation: calls.append(("publish", mid, generation))
    )

    fake.on_connect(fake, None, None, 0, None)
    fake.on_publish(fake, None, 42, None, None)

    assert fake.subscriptions == [
        ("device/+/status", 1),
        ("device/+/telemetry", 1),
    ]
    assert calls == []
    loop.run_scheduled()
    assert calls == [
        ("connect", None),
        ("publish", 42, client.publish_session_generation),
    ]


def test_publish_ack_queue_preserves_the_source_client_session_generation():
    loop = FakeRunningLoop()
    old_paho = FakePahoClient()
    new_paho = FakePahoClient()
    paho_clients = iter((old_paho, new_paho))
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        XiaoxinDeviceRegistry(),
        tenant=load_tenant_config(
            {
                "xiaoxin_control": {
                    "doorbell_mqtt": {"endpoint": "localhost:1883"}
                }
            }
        ),
        client_factory=lambda: next(paho_clients),
    )
    calls = []
    client.add_publish_ack_listener(
        lambda mid, generation: calls.append((mid, generation))
    )

    client.start(loop)
    old_generation = client.publish_session_generation
    old_paho.on_publish(old_paho, None, 1, None, None)
    client.stop()
    client.start(loop)
    new_generation = client.publish_session_generation
    new_paho.on_publish(new_paho, None, 1, None, None)

    assert calls == []
    assert new_generation > old_generation
    loop.run_scheduled()
    assert calls == [(1, old_generation), (1, new_generation)]


def test_closed_runtime_loop_does_not_abort_remaining_listener_scheduling():
    loop = ClosingRuntimeLoop()
    client, fake = started_client(loop=loop)
    client.add_publish_ack_listener(lambda mid, generation: None)
    client.add_publish_ack_listener(lambda mid, generation: None)
    escaped_error = None

    try:
        fake.on_publish(fake, None, 43, None, None)
    except RuntimeError as exc:
        escaped_error = exc

    assert (escaped_error, loop.schedule_attempts) == (None, 2)


def test_diagnostic_state_distinguishes_disabled_and_not_started():
    tenant = load_tenant_config({"xiaoxin_control": {}})
    disabled = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host=""),
        XiaoxinDeviceRegistry(),
        tenant=tenant,
    )
    enabled = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost"),
        XiaoxinDeviceRegistry(),
        tenant=load_tenant_config(
            {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "localhost:1883"}}}
        ),
    )

    assert disabled.diagnostic_state() == "doorbell_mqtt_disabled"
    assert enabled.diagnostic_state() == "doorbell_client_not_started"


def test_start_does_not_crash_when_broker_refuses_connection():
    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="localhost", port=1883),
        XiaoxinDeviceRegistry(),
        tenant=load_tenant_config(
            {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "localhost:1883"}}}
        ),
        client_factory=RefusingPahoClient,
    )

    client.start_without_loop_for_test()

    assert client.diagnostic_state() == "doorbell_connect_failed"
    assert client.publish_wake("aa:bb") is False


def test_publish_wake_retries_connection_after_startup_refusal():
    registry = XiaoxinDeviceRegistry()
    fake = FakePahoClient()
    attempts = [RefusingPahoClient(), fake]

    client = XiaoxinDoorbellClient(
        DoorbellMqttSettings(host="xiaoxin-doorbell-mqtt", port=1883),
        registry,
        tenant=load_tenant_config(
            {"xiaoxin_control": {"doorbell_mqtt": {"endpoint": "localhost:1883"}}}
        ),
        client_factory=lambda: attempts.pop(0),
    )
    client.start_without_loop_for_test()

    assert client.diagnostic_state() == "doorbell_connect_failed"
    assert client.publish_wake("aa:bb") is True
    assert client.diagnostic_state() == "ok"
    assert fake.host == "xiaoxin-doorbell-mqtt"
