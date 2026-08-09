# Xiaoxin Reliable Notification TTS Contract

## Scope and deployment topology

This contract defines reliable reminder-card delivery and TTS playback for Xiaoxin devices. It provides an in-process reliability guarantee only: ordinary device connection replacement is retried while the same server process remains alive, but delivery attempts are not persisted across a server process restart.

The current deployment has two independent transport roles:

- Doorbell MQTT is only the independent notification/wake transport used to bring an offline device back to its application connection.
- The reminder card JSON, TTS `start`/`stop` JSON, binary TTS audio, and device ACK JSON all use the same ordered WebSocket/TCP application path.

The firmware `MqttProtocol` UDP audio gateway is a legacy path and is disabled in the current deployment because `server.mqtt_gateway` is `null`. It is not the Doorbell MQTT transport. Reliable playback must not add a `final_sequence` marker or a fixed 180 ms tail delay; ordering and completion come from the WebSocket/TCP stream plus the ready/error/done state machine.

## Reliable capability negotiation

A device is eligible for strong reliable notification playback exactly when its hello advertises all three required boolean flags as true: `tts_ready_ack`, `tts_done_ack`, and `tts_preroll_buffer`. The hello may also include optional pre-roll capacity metadata:

```json
{
  "features": {
    "tts_ready_ack": true,
    "tts_done_ack": true,
    "tts_preroll_buffer": true,
    "tts_preroll_capacity_ms": 5040
  }
}
```

- `tts_ready_ack` means the device reports when the playback path is ready to accept the ordered audio stream.
- `tts_done_ack` means the device reports only after playback is physically drained.
- `tts_preroll_buffer` means audio arriving between `start` ownership and `ready` can be retained in order.
- `tts_preroll_capacity_ms`, when present, is diagnostic metadata describing the advertised pre-roll duration. The current server does not use it for reliable-eligibility decisions; a missing, zero, or non-positive value does not by itself force `legacy_unverified`.

Each required value must be the JSON boolean `true` exactly. Strings such as `"true"`, numbers such as `1`, objects, `null`, missing values, and `false` all fail closed to `legacy_unverified`. Legacy devices remain compatible, but are outside the strong guarantee and must never be reported as reliably played.

## Reminder card and delivery ACK

Each reminder card uses `type=xiaoxin_event` and carries the delivery's stable `delivery_id`. The device uses that same stable id for the card notification identity, so retransmission updates the existing card instead of creating a duplicate, and acknowledges receipt with:

```json
{
  "type": "xiaoxin_ack",
  "delivery_id": "stable-delivery-id",
  "state": "device_received",
  "reason": null
}
```

Card receipt and TTS playback completion are independent gates. A speaking delivery becomes `done` only after the matching `device_received` card ACK and the matching device TTS `done` ACK have both arrived.

## TTS messages and ACK correlation

The server preserves the compatible TTS control shape and adds a required sentence correlation id for reliable attempts:

```json
{"type":"tts","state":"start","session_id":"...","sentence_id":"..."}
{"type":"tts","state":"stop","session_id":"...","sentence_id":"..."}
```

The device replies on the same application connection:

```json
{"type":"tts","state":"ready","session_id":"...","sentence_id":"..."}
{"type":"tts","state":"done","session_id":"...","sentence_id":"..."}
{"type":"tts","state":"error","session_id":"...","sentence_id":"...","reason":"preroll_overflow"}
```

ACK matching uses the current connection/session together with `sentence_id` and `state`. An ACK from an old connection, old session, old sentence, wrong phase, or malformed payload cannot complete the current attempt.

For each reliable sentence the server enforces the phase sequence `READY_WAIT -> STREAMING -> DONE_WAIT -> TERMINAL`:

- `ready` is accepted only in `READY_WAIT`. A timed wait between idempotent same-ID `start` retries leaves that attempt eligible to consume a late `ready` on the next retry.
- after the exact matching `ready`, the server enters `STREAMING`; a `done` received before the server has drained its outgoing audio and armed the stop waiter is ignored and is never cached.
- immediately before sending `stop`, the server enters `DONE_WAIT` and registers the matching future; `done` is accepted only in this phase.
- `error` is accepted in any active nonterminal phase, including `STREAMING`, and moves the sentence to `TERMINAL` exactly once. A duplicate error or later done cannot replace that terminal failure.

The ACK history TTL applies only to completed `TERMINAL` lifecycle history. It never expires `READY_WAIT`, `STREAMING`, or `DONE_WAIT` solely because an active attempt has taken longer than the history TTL. Connection close and explicit attempt failure terminalize every active sentence; an active ready/done waiter completes with a typed `state=error` result rather than cancellation, so dispatcher retry ownership is preserved.

Reliable TTS attempts are serialized per device from connection acquisition through terminal playback outcome. Attempts for different devices remain concurrent. Before a reliable attempt sends `start`, the server cancels and awaits the previous rate-controlled sender, clears stale queued TTS text/audio, and installs sentence ownership checks before every binary send so an old captured sender cannot splice packets into the new attempt.

Device failures use `state=error` and an enumerated reason. The implemented firmware reasons are:

- `preroll_overflow`: the reliable TTS ingress/pre-roll queue reached its fixed packet capacity;
- `pipeline_reset_timeout`: playback preparation could not drain the reset audio pipeline within its device-side timeout;
- `decoder_create_failed`: the firmware could not create the decoder required for the current reliable TTS attempt;
- `decode_failed`: an encoded audio packet could not be decoded for playback;
- `resampler_create_failed`: the firmware could not create the resampler required to match the playback output format;
- `output_write_timeout`: decoded audio could not be written to the device output path before the firmware timeout;
- `drain_task_create_failed`: the firmware could not create the background task that drains the final playback state;
- `playback_drain_timeout`: ordered ingress or the local playback path did not fully drain within the device-side completion timeout;
- `superseded`: a new start with a different `sentence_id` replaced an older sentence that was still in progress; the error ACK identifies the older sentence;
- `stale_start`: a start reused a `sentence_id` previously made stale by supersession or abort, so the firmware rejected it instead of restarting that attempt.

New reasons require an explicit contract update rather than free-form success/failure text.

## Ready semantics

Receiving `tts:start` synchronously transfers ownership of subsequent binary audio to the reliable TTS session before main-task scheduling. Duplicate `start` with the same `sentence_id` is idempotent.

Before sending `ready`, the device must:

1. Stop microphone upload for the playback transition.
2. Exit the low-power clock and stop its refresh timer where applicable.
3. Own and preserve ordered incoming audio in pre-roll.
4. Clear stale playback state and reset the decoder.
5. Activate the ordered ingress pump and make the decoder, playback queue, active output, amplifier, and I2S path ready.

The server does not enqueue the reminder text before the matching `ready`. A ready timeout does not enqueue text and does not complete the delivery; it fails only the current attempt. The server may resend the same idempotent `start` after delays of 300, 600, and 1200 ms, but exhaustion still fails that attempt.

## Done and replay semantics

After `tts:stop`, the device sends `done` only after all of the following are drained:

1. pre-roll and ordered ingress;
2. decode work and decode-queue backpressure;
3. the playback queue;
4. active audio output;
5. the expected I2S playback time for the final samples.

Handing the last sample to a software queue is not completion. The server waits up to `tts_done_ack_timeout_ms` (10 seconds) for the matching device `done`.

A done timeout, device error, or application-connection close fails the current attempt. It never performs success cleanup and never marks delivery playback complete. The next attempt creates a new `sentence_id` and restarts from the full original text at its first character; audio from different attempts must never be spliced.

## Retry lifetime

Delivery retry delays are 2, 5, 15, and 30 seconds. The 30-second value is a delay cap, not a retry-count cap: subsequent failures continue retrying at the capped delay without a maximum attempt count while the service process lives.

An ordinary connection replacement does not discard the in-process delivery task. The task binds the next attempt to the new current connection and a new `sentence_id`. A server process restart does discard this in-memory retry state because this contract introduces no persistence.

## Configuration defaults

All supported server configurations declare the same values:

```yaml
tts_ready_ack_timeout_ms: 700
tts_ready_start_retry_delays_ms: [300, 600, 1200]
tts_delivery_retry_delays_ms: [2000, 5000, 15000, 30000]
tts_done_ack_timeout_ms: 10000
```

Done timing begins only after `tts:stop`; there is no separate control-console done-timeout setting.
