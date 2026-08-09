# Notification TTS Reliable Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让主动提醒在 ESP32 退出低功耗时钟屏保后从首帧完整播放，并在 ready 超时、缓冲溢出、连接中断或 done 超时时从句首持续重试，直到当前服务进程收到设备真实播放完成 ACK。

**Architecture:** 固件提供 `tts ready/error/done`、有序 ingress/pre-roll 状态机、屏保唤醒和软件队列加 I2S 时间线 drain fence；服务端把提醒 delivery 与每次 `sentence_id` attempt 分离，只有当前连接、当前 attempt 的匹配 done 才完成语音投递。固件能力先落地并通过测试，服务端随后以三个 hello feature flag 为强可靠模式开关，旧固件继续走 `legacy_unverified` 兼容路径。

**Tech Stack:** Python 3.12、`asyncio`、`pytest`、ESP-IDF C++17、FreeRTOS event groups/tasks、cJSON、LVGL、I2S、现有 Qwen realtime TTS 与 `AudioRateController`。

## Global Constraints

- 服务端仓库：`D:\AI_Pet\xiaoxin-esp32-server`；固件仓库：`D:\AI_Pet\hzcu_xiaoxin_firmwire_private`。
- 不修改或暂存服务端现有用户文件 `docs/README.md` 与 `docs/operations/xiaoxin-real-device-acceptance-ledger.md`。
- 强可靠模式必须同时要求 `tts_ready_ack=true`、`tts_done_ack=true`、`tts_preroll_buffer=true`。
- pre-roll/有序 ingress 上限固定为 84 个 60 ms Opus 包，即 5040 ms。
- ready 等待固定为 700 ms；同一 attempt 的 start 重发间隔固定为 300/600/1200 ms，共四次 start 发送。
- delivery attempt 重试间隔固定为 2/5/15/30 秒；30 秒是间隔上限，不是次数上限。
- done ACK 等待固定为 10000 ms；固件本地 drain watchdog 固定为 8000 ms，给 ACK 传输留出 2 秒余量。
- 每次从头重播必须创建新的 `sentence_id`，不得续传旧 attempt 的音频位置。
- 只有 pre-roll、decode、playback、当前输出任务和 I2S 预计播放时间线全部排空后才能发送 done。
- 跨服务进程重启的 delivery 持久化不在本轮范围；同一服务进程内不得设置最大 attempt 数或静默过期。
- 退出屏保直接隐藏低功耗时钟图层，不新增转场动画，不强制关闭用户已打开的设置页或通知页。
- 所有生产变更按测试先行；每个任务结束后只提交该任务列出的文件。

## File Structure Map

### Firmware repository

- `main/protocols/protocol.h/.cc`：统一序列化 TTS ACK，避免 WebSocket/MQTT 各自拼 JSON。
- `main/protocols/websocket_protocol.cc`、`main/protocols/mqtt_protocol.cc`：hello 能力声明。
- `main/boards/common/board.h`：板级 `PrepareForAudioPlayback()` 深接口。
- `main/boards/waveshare/esp32-s3-touch-lcd-1.46/esp32-s3-touch-lcd-1.46.cc`：目标板屏保退出、定时器停止、背光与性能模式恢复。
- `main/audio/tts_playback_session.h/.cc`：与 `DeviceState` 分离的 attempt 状态、幂等 start、84 包有序 ingress、终态 ACK 缓存。
- `main/audio/audio_service.h/.cc`：无损 decode 入队、decode 可用通知、输出任务 busy 状态与播放时间线 drain fence。
- `main/application.h/.cc`：协议回调编排、同步 PREPARING、异步准备、pump、drain task 和代际检查。
- `tests/xiaoxin_tts_reliable_playback_path_test.py`：固件源路径/集成约束测试。
- `tests/tts_playback_session_test.cc`、`tests/stubs/protocol.h`：纯 C++ 状态机行为测试。

### Server repository

- `main/xiaozhi-server/core/xiaoxin/tts_delivery.py`：ACK 结果、attempt 异常和 attempt outcome 类型。
- `main/xiaozhi-server/core/connection.py`：能力判断、ACK waiter、同 sentence start 短重试、连接关闭失败通知。
- `main/xiaozhi-server/core/handle/textHandler/ttsMessageHandler.py`：ready/done/error 校验与当前 session 匹配。
- `main/xiaozhi-server/core/handle/sendAudioHandle.py`：stop 后读取 done 结果，不再把超时当成功。
- `main/xiaozhi-server/core/xiaoxin/control_types.py`：delivery 的事件 ACK、TTS attempt 和播放模式字段。
- `main/xiaozhi-server/core/xiaoxin/delivery_store.py`：原子记录 attempt 开始、失败、重试与完成。
- `main/xiaozhi-server/core/xiaoxin/dispatcher.py`：事件卡片与语音的独立重试循环、在线暂停和双条件完成。
- `main/xiaozhi-server/core/xiaoxin/control_runtime.py`：配置注入与 shutdown 时取消长期重试任务。
- `main/xiaozhi-server/config.yaml`、`main/xiaozhi-server/data/.config.yaml`、`main/xiaozhi-server/config_from_api.yaml`：可靠 TTS 参数。
- `main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py`、`test_connection_ack.py`、`test_dispatcher.py`、`test_registry_and_store.py`、`test_config_contract.py`：服务端回归覆盖。
- `docs/development/xiaoxin-tts-playback-ack.md`：将基础 ACK 文档更新到方案 C 语义。

---

### Task 1: Firmware TTS ACK serialization and capability advertisement

**Files:**
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\protocols\protocol.h:39-96`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\protocols\protocol.cc:43-71`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\protocols\websocket_protocol.cc:240-258`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\protocols\mqtt_protocol.cc:297-314`
- Create: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_tts_reliable_playback_path_test.py`

**Interfaces:**
- Produces: `void Protocol::SendTtsAck(const std::string& state, const std::string& sentence_id, const std::string& reason = "")`.
- Produces hello fields: `tts_ready_ack`, `tts_done_ack`, `tts_preroll_buffer`, `tts_preroll_capacity_ms=5040`.
- Consumed by: Task 5 `Application` integration and Task 6 server capability detection.

- [ ] **Step 1: Write failing protocol path tests**

```python
from pathlib import Path

PROTOCOL_H = Path("main/protocols/protocol.h")
PROTOCOL_CC = Path("main/protocols/protocol.cc")
WEBSOCKET = Path("main/protocols/websocket_protocol.cc")
MQTT = Path("main/protocols/mqtt_protocol.cc")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_protocol_exposes_correlated_tts_ack_sender():
    header = read(PROTOCOL_H)
    source = read(PROTOCOL_CC)
    assert "void SendTtsAck(const std::string& state," in header
    assert 'cJSON_AddStringToObject(root, "type", "tts");' in source
    assert 'cJSON_AddStringToObject(root, "session_id", session_id_.c_str());' in source
    assert 'cJSON_AddStringToObject(root, "sentence_id", sentence_id.c_str());' in source
    assert 'cJSON_AddStringToObject(root, "reason", reason.c_str());' in source


def test_both_transports_advertise_reliable_tts_capabilities():
    for source in (read(WEBSOCKET), read(MQTT)):
        assert 'cJSON_AddBoolToObject(features, "tts_ready_ack", true);' in source
        assert 'cJSON_AddBoolToObject(features, "tts_done_ack", true);' in source
        assert 'cJSON_AddBoolToObject(features, "tts_preroll_buffer", true);' in source
        assert 'cJSON_AddNumberToObject(features, "tts_preroll_capacity_ms", 5040);' in source
```

- [ ] **Step 2: Run the tests and verify they fail**

Run from `D:\AI_Pet\hzcu_xiaoxin_firmwire_private`:

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py -q
```

Expected: two failures because `SendTtsAck` and the four feature fields do not exist.

- [ ] **Step 3: Add the public protocol ACK interface**

Insert in the public section of `Protocol`:

```cpp
void SendTtsAck(const std::string& state,
                const std::string& sentence_id,
                const std::string& reason = "");
```

Add to `protocol.cc`:

```cpp
void Protocol::SendTtsAck(const std::string& state,
                          const std::string& sentence_id,
                          const std::string& reason) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "tts");
    cJSON_AddStringToObject(root, "state", state.c_str());
    cJSON_AddStringToObject(root, "session_id", session_id_.c_str());
    cJSON_AddStringToObject(root, "sentence_id", sentence_id.c_str());
    if (!reason.empty()) {
        cJSON_AddStringToObject(root, "reason", reason.c_str());
    }
    char* text = cJSON_PrintUnformatted(root);
    if (text != nullptr) {
        SendText(text);
        cJSON_free(text);
    }
    cJSON_Delete(root);
}
```

- [ ] **Step 4: Advertise identical capabilities on WebSocket and MQTT**

Immediately after the existing `mcp` feature in both `GetHelloMessage()` implementations add:

```cpp
cJSON_AddBoolToObject(features, "tts_ready_ack", true);
cJSON_AddBoolToObject(features, "tts_done_ack", true);
cJSON_AddBoolToObject(features, "tts_preroll_buffer", true);
cJSON_AddNumberToObject(features, "tts_preroll_capacity_ms", 5040);
```

- [ ] **Step 5: Run the focused firmware tests**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py tests/xiaoxin_protocol_compatibility_test.py tests/xiaoxin_device_time_protocol_test.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the protocol capability slice**

```powershell
git add main/protocols/protocol.h main/protocols/protocol.cc main/protocols/websocket_protocol.cc main/protocols/mqtt_protocol.cc tests/xiaoxin_tts_reliable_playback_path_test.py
git commit -m "feat: advertise reliable tts playback acknowledgements"
```

---

### Task 2: Firmware board-level audio playback preparation

**Files:**
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\boards\common\board.h:53-82`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\boards\waveshare\esp32-s3-touch-lcd-1.46\esp32-s3-touch-lcd-1.46.cc:4934-6160`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_tts_reliable_playback_path_test.py`
- Test: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_low_power_clock_visual_path_test.py`

**Interfaces:**
- Produces: `virtual void Board::PrepareForAudioPlayback()` with default no-op.
- Produces target override that wakes `PowerSaveTimer`, hides the clock layer, stops its 50 ms timer through `HideLowPowerClockScreen()`, restores brightness and selects PERFORMANCE.
- Consumed by: Task 5 `Application::PrepareTtsPlayback()`.

- [ ] **Step 1: Add failing board preparation tests**

Append:

```python
BOARD_H = Path("main/boards/common/board.h")
WAVESHARE_146 = Path(
    "main/boards/waveshare/esp32-s3-touch-lcd-1.46/esp32-s3-touch-lcd-1.46.cc"
)


def test_board_exposes_default_audio_playback_preparation_hook():
    assert "virtual void PrepareForAudioPlayback() {}" in read(BOARD_H)


def test_waveshare_audio_preparation_stops_screensaver_work():
    source = read(WAVESHARE_146)
    start = source.index("void PrepareForAudioPlayback() override")
    end = source.index("virtual AudioCodec* GetAudioCodec() override", start)
    body = source[start:end]
    assert "power_save_timer_->WakeUp();" in body
    assert "display->HideLowPowerClockScreen();" in body
    assert "WifiBoard::SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);" in body


def test_waveshare_performance_level_also_wakes_power_save_timer():
    source = read(WAVESHARE_146)
    start = source.index("void SetPowerSaveLevel(PowerSaveLevel level) override")
    end = source.index("void PrepareForAudioPlayback() override", start)
    body = source[start:end]
    assert "if (level != PowerSaveLevel::LOW_POWER)" in body
    assert "power_save_timer_->WakeUp();" in body
    assert "WifiBoard::SetPowerSaveLevel(level);" in body
```

- [ ] **Step 2: Verify the board tests fail**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py -q
```

Expected: the three new tests fail.

- [ ] **Step 3: Add the deep board interface**

Add before `SetPowerSaveLevel` in `Board`:

```cpp
virtual void PrepareForAudioPlayback() {}
```

- [ ] **Step 4: Implement the Waveshare 1.46 override**

Add in the public section of `CustomBoard` before `GetAudioCodec()`:

```cpp
void SetPowerSaveLevel(PowerSaveLevel level) override {
    if (level != PowerSaveLevel::LOW_POWER && power_save_timer_ != nullptr) {
        power_save_timer_->WakeUp();
    }
    WifiBoard::SetPowerSaveLevel(level);
}

void PrepareForAudioPlayback() override {
    if (power_save_timer_ != nullptr) {
        power_save_timer_->WakeUp();
    }
    auto* display = static_cast<PaopaoPetDisplay*>(display_);
    if (display != nullptr) {
        display->HideLowPowerClockScreen();
    }
    WifiBoard::SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
}
```

`HideLowPowerClockScreen()` already calls `StopLowPowerClockRefreshTimer()` and `RestoreBrightness()`, so do not duplicate those operations in `CustomBoard`.

- [ ] **Step 5: Run focused screen and playback preparation tests**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py tests/xiaoxin_low_power_clock_visual_path_test.py -q
```

Expected: all tests pass; existing low-power clock behavior remains intact.

- [ ] **Step 6: Commit the board wake slice**

```powershell
git add main/boards/common/board.h main/boards/waveshare/esp32-s3-touch-lcd-1.46/esp32-s3-touch-lcd-1.46.cc tests/xiaoxin_tts_reliable_playback_path_test.py
git commit -m "feat: wake display before audio playback"
```

---

### Task 3: Firmware TTS attempt state and ordered ingress module

**Files:**
- Create: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\audio\tts_playback_session.h`
- Create: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\audio\tts_playback_session.cc`
- Create: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\stubs\protocol.h`
- Create: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\tts_playback_session_test.cc`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\CMakeLists.txt`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_tts_reliable_playback_path_test.py`

**Interfaces:**
- Produces: `TtsPlaybackSession::Start`, `Enqueue`, `Pump`, `MarkPlaying`, `BeginDraining`, `WaitForIngressEmpty`, `Complete`, `AbortCurrent`.
- `Start` owns sentence idempotency and monotonic `generation`; `Application` must check generation before every scheduled action.
- `Pump` accepts `std::function<bool(std::unique_ptr<AudioStreamPacket>&)>`; the sink moves ownership only on success, so a full decode queue cannot destroy the front packet.
- Consumed by: Tasks 4 and 5.

- [ ] **Step 1: Write the host-test protocol stub**

```cpp
#ifndef TEST_PROTOCOL_STUB_H
#define TEST_PROTOCOL_STUB_H

#include <cstdint>
#include <vector>

struct AudioStreamPacket {
    int sample_rate = 16000;
    int frame_duration = 60;
    uint32_t timestamp = 0;
    std::vector<uint8_t> payload;
};

#endif
```

- [ ] **Step 2: Write failing state and ordering tests**

```cpp
#include <assert.h>
#include <memory>
#include <string>
#include <vector>

#include "tts_playback_session.h"

static std::unique_ptr<AudioStreamPacket> packet(uint8_t id) {
    auto value = std::make_unique<AudioStreamPacket>();
    value->payload = {id};
    return value;
}

static void new_start_enters_preparing_and_duplicate_is_idempotent() {
    TtsPlaybackSession session;
    auto first = session.Start("s1");
    auto duplicate = session.Start("s1");
    assert(first.action == TtsStartAction::kPrepare);
    assert(duplicate.action == TtsStartAction::kContinuePreparing);
    assert(first.generation == duplicate.generation);
}

static void final_sentence_replays_ack_without_restarting() {
    TtsPlaybackSession session;
    auto first = session.Start("s1");
    assert(session.MarkPlaying(first.generation));
    assert(session.BeginDraining("s1", first.generation));
    assert(session.Complete(first.generation, "done", ""));
    auto replay = session.Start("s1");
    assert(replay.action == TtsStartAction::kReplayFinal);
    assert(replay.final_ack.state == "done");
}

static void failed_sentence_replays_the_same_error_without_restarting() {
    TtsPlaybackSession session;
    auto first = session.Start("s1");
    assert(session.Fail(first.generation, "preroll_overflow"));
    auto replay = session.Start("s1");
    assert(replay.action == TtsStartAction::kReplayFinal);
    assert(replay.final_ack.state == "error");
    assert(replay.final_ack.reason == "preroll_overflow");
}

static void aborted_sentence_requires_a_new_sentence_id() {
    TtsPlaybackSession session;
    session.Start("s1");
    session.AbortCurrent("connection_closed");
    assert(session.Start("s1").action == TtsStartAction::kRejectStale);
    assert(session.Start("s2").action == TtsStartAction::kPrepare);
}

static void older_completed_sentence_stays_terminal_after_newer_completion() {
    TtsPlaybackSession session;
    auto first = session.Start("s1");
    assert(session.MarkPlaying(first.generation));
    assert(session.BeginDraining("s1", first.generation));
    assert(session.Complete(first.generation, "done", ""));
    auto second = session.Start("s2");
    assert(session.MarkPlaying(second.generation));
    assert(session.BeginDraining("s2", second.generation));
    assert(session.Complete(second.generation, "done", ""));
    assert(session.Start("s1").action == TtsStartAction::kReplayFinal);
}

static void ingress_preserves_order_when_sink_temporarily_fills() {
    TtsPlaybackSession session;
    auto start = session.Start("s1");
    assert(session.Enqueue(packet(1)) == TtsIngressResult::kAccepted);
    assert(session.Enqueue(packet(2)) == TtsIngressResult::kAccepted);
    assert(session.MarkPlaying(start.generation));
    std::vector<uint8_t> output;
    int available = 1;
    auto sink = [&output, &available](std::unique_ptr<AudioStreamPacket>& item) {
        if (available == 0) return false;
        output.push_back(item->payload.front());
        item.reset();
        --available;
        return true;
    };
    assert(session.Pump(sink) == 1);
    assert(output == std::vector<uint8_t>({1}));
    available = 1;
    assert(session.Enqueue(packet(3)) == TtsIngressResult::kAccepted);
    assert(session.Pump(sink) == 1);
    available = 1;
    assert(session.Pump(sink) == 1);
    assert(output == std::vector<uint8_t>({1, 2, 3}));
}

static void eighty_fifth_buffered_packet_overflows_without_dropping_head() {
    TtsPlaybackSession session;
    session.Start("s1");
    for (int i = 0; i < 84; ++i) {
        assert(session.Enqueue(packet(static_cast<uint8_t>(i))) == TtsIngressResult::kAccepted);
    }
    assert(session.Enqueue(packet(84)) == TtsIngressResult::kOverflow);
    assert(session.buffered_packets() == 84);
}

int main() {
    new_start_enters_preparing_and_duplicate_is_idempotent();
    final_sentence_replays_ack_without_restarting();
    failed_sentence_replays_the_same_error_without_restarting();
    aborted_sentence_requires_a_new_sentence_id();
    older_completed_sentence_stays_terminal_after_newer_completion();
    ingress_preserves_order_when_sink_temporarily_fills();
    eighty_fifth_buffered_packet_overflows_without_dropping_head();
    return 0;
}
```

- [ ] **Step 3: Run the host test and verify compilation fails**

```powershell
New-Item -ItemType Directory -Force build\host-tests | Out-Null
g++ -std=c++17 -I tests/stubs -I main/audio tests/tts_playback_session_test.cc main/audio/tts_playback_session.cc -o build/host-tests/tts_playback_session_test.exe
```

Expected: compilation fails because `tts_playback_session.h/.cc` do not exist.

- [ ] **Step 4: Add the complete public session header**

```cpp
#ifndef TTS_PLAYBACK_SESSION_H
#define TTS_PLAYBACK_SESSION_H

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

#include "protocol.h"

enum class TtsPlaybackPhase { kIdle, kPreparing, kPlaying, kDraining };
enum class TtsStartAction {
    kPrepare,
    kContinuePreparing,
    kResendReady,
    kContinueDraining,
    kReplayFinal,
    kRejectStale,
};
enum class TtsIngressResult { kAccepted, kOverflow, kIgnored };

struct TtsFinalAck {
    std::string sentence_id;
    std::string state;
    std::string reason;
};

struct TtsStartDecision {
    TtsStartAction action = TtsStartAction::kRejectStale;
    uint32_t generation = 0;
    std::string superseded_sentence_id;
    TtsFinalAck final_ack;
};

class TtsPlaybackSession {
public:
    static constexpr size_t kMaxBufferedPackets = 84;
    static constexpr size_t kAckHistorySize = 8;
    using PacketSink = std::function<bool(std::unique_ptr<AudioStreamPacket>&)>;

    TtsStartDecision Start(const std::string& sentence_id);
    TtsIngressResult Enqueue(std::unique_ptr<AudioStreamPacket> packet);
    size_t Pump(const PacketSink& sink);
    bool MarkPlaying(uint32_t generation);
    bool BeginDraining(const std::string& sentence_id, uint32_t generation);
    bool WaitForIngressEmpty(uint32_t generation, std::chrono::milliseconds timeout);
    bool Complete(uint32_t generation, const std::string& state, const std::string& reason);
    bool Fail(uint32_t generation, const std::string& reason);
    void AbortCurrent(const std::string& reason);

    bool IsCurrent(uint32_t generation, const std::string& sentence_id) const;
    bool OwnsPlaybackPipeline() const;
    uint32_t generation() const;
    std::string sentence_id() const;
    TtsPlaybackPhase phase() const;
    size_t buffered_packets() const;
    TtsFinalAck FinalAckFor(const std::string& sentence_id) const;

private:
    mutable std::mutex mutex_;
    std::condition_variable ingress_empty_cv_;
    TtsPlaybackPhase phase_ = TtsPlaybackPhase::kIdle;
    uint32_t generation_ = 0;
    std::string sentence_id_;
    std::deque<std::string> stale_sentence_ids_;
    std::deque<TtsFinalAck> final_acks_;
    std::deque<std::unique_ptr<AudioStreamPacket>> ingress_queue_;

    TtsFinalAck FindFinalLocked(const std::string& sentence_id) const;
    bool IsStaleLocked(const std::string& sentence_id) const;
    void RememberFinalLocked(const TtsFinalAck& ack);
    void RememberStaleLocked(const std::string& sentence_id);
};

#endif
```

- [ ] **Step 5: Implement the session rules without ESP dependencies**

Implement `tts_playback_session.cc` with these exact rules:

```cpp
#include "tts_playback_session.h"

TtsStartDecision TtsPlaybackSession::Start(const std::string& sentence_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    TtsStartDecision decision;
    decision.generation = generation_;
    if (sentence_id.empty()) return decision;
    const TtsFinalAck completed = FindFinalLocked(sentence_id);
    if (!completed.state.empty()) {
        decision.action = TtsStartAction::kReplayFinal;
        decision.final_ack = completed;
        return decision;
    }
    if (IsStaleLocked(sentence_id)) {
        decision.action = TtsStartAction::kRejectStale;
        return decision;
    }
    if (sentence_id == sentence_id_) {
        if (phase_ == TtsPlaybackPhase::kPreparing) decision.action = TtsStartAction::kContinuePreparing;
        if (phase_ == TtsPlaybackPhase::kPlaying) decision.action = TtsStartAction::kResendReady;
        if (phase_ == TtsPlaybackPhase::kDraining) decision.action = TtsStartAction::kContinueDraining;
        return decision;
    }
    if (phase_ != TtsPlaybackPhase::kIdle) {
        decision.superseded_sentence_id = sentence_id_;
        RememberStaleLocked(sentence_id_);
    }
    ingress_queue_.clear();
    sentence_id_ = sentence_id;
    phase_ = TtsPlaybackPhase::kPreparing;
    ++generation_;
    decision.action = TtsStartAction::kPrepare;
    decision.generation = generation_;
    ingress_empty_cv_.notify_all();
    return decision;
}

TtsIngressResult TtsPlaybackSession::Enqueue(std::unique_ptr<AudioStreamPacket> packet) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!packet || (phase_ != TtsPlaybackPhase::kPreparing && phase_ != TtsPlaybackPhase::kPlaying)) {
        return TtsIngressResult::kIgnored;
    }
    if (ingress_queue_.size() >= kMaxBufferedPackets) return TtsIngressResult::kOverflow;
    ingress_queue_.push_back(std::move(packet));
    return TtsIngressResult::kAccepted;
}

size_t TtsPlaybackSession::Pump(const PacketSink& sink) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (phase_ != TtsPlaybackPhase::kPlaying && phase_ != TtsPlaybackPhase::kDraining) return 0;
    size_t moved = 0;
    while (!ingress_queue_.empty() && sink(ingress_queue_.front())) {
        ingress_queue_.pop_front();
        ++moved;
    }
    if (ingress_queue_.empty()) ingress_empty_cv_.notify_all();
    return moved;
}

bool TtsPlaybackSession::MarkPlaying(uint32_t generation) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (generation != generation_ || phase_ != TtsPlaybackPhase::kPreparing) return false;
    phase_ = TtsPlaybackPhase::kPlaying;
    return true;
}

bool TtsPlaybackSession::BeginDraining(const std::string& sentence_id, uint32_t generation) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (generation != generation_ || sentence_id != sentence_id_ || phase_ != TtsPlaybackPhase::kPlaying) return false;
    phase_ = TtsPlaybackPhase::kDraining;
    return true;
}

bool TtsPlaybackSession::WaitForIngressEmpty(uint32_t generation, std::chrono::milliseconds timeout) {
    std::unique_lock<std::mutex> lock(mutex_);
    return ingress_empty_cv_.wait_for(lock, timeout, [this, generation]() {
        return generation != generation_ || ingress_queue_.empty();
    }) && generation == generation_ && ingress_queue_.empty();
}

bool TtsPlaybackSession::Complete(uint32_t generation, const std::string& state, const std::string& reason) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (generation != generation_ || phase_ != TtsPlaybackPhase::kDraining) return false;
    RememberFinalLocked({sentence_id_, state, reason});
    sentence_id_.clear();
    ingress_queue_.clear();
    phase_ = TtsPlaybackPhase::kIdle;
    ingress_empty_cv_.notify_all();
    return true;
}

bool TtsPlaybackSession::Fail(uint32_t generation, const std::string& reason) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (generation != generation_ || phase_ == TtsPlaybackPhase::kIdle) return false;
    RememberFinalLocked({sentence_id_, "error", reason});
    sentence_id_.clear();
    ingress_queue_.clear();
    phase_ = TtsPlaybackPhase::kIdle;
    ingress_empty_cv_.notify_all();
    return true;
}

void TtsPlaybackSession::AbortCurrent(const std::string&) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!sentence_id_.empty()) RememberStaleLocked(sentence_id_);
    sentence_id_.clear();
    ingress_queue_.clear();
    phase_ = TtsPlaybackPhase::kIdle;
    ++generation_;
    ingress_empty_cv_.notify_all();
}

bool TtsPlaybackSession::IsCurrent(uint32_t generation, const std::string& sentence_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return generation == generation_ && sentence_id == sentence_id_;
}

bool TtsPlaybackSession::OwnsPlaybackPipeline() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return phase_ == TtsPlaybackPhase::kPlaying || phase_ == TtsPlaybackPhase::kDraining;
}

uint32_t TtsPlaybackSession::generation() const { std::lock_guard<std::mutex> lock(mutex_); return generation_; }
std::string TtsPlaybackSession::sentence_id() const { std::lock_guard<std::mutex> lock(mutex_); return sentence_id_; }
TtsPlaybackPhase TtsPlaybackSession::phase() const { std::lock_guard<std::mutex> lock(mutex_); return phase_; }
size_t TtsPlaybackSession::buffered_packets() const { std::lock_guard<std::mutex> lock(mutex_); return ingress_queue_.size(); }
TtsFinalAck TtsPlaybackSession::FinalAckFor(const std::string& sentence_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return FindFinalLocked(sentence_id);
}

TtsFinalAck TtsPlaybackSession::FindFinalLocked(
    const std::string& sentence_id) const {
    for (const auto& ack : final_acks_) {
        if (ack.sentence_id == sentence_id) return ack;
    }
    return {};
}

bool TtsPlaybackSession::IsStaleLocked(const std::string& sentence_id) const {
    for (const auto& stale : stale_sentence_ids_) {
        if (stale == sentence_id) return true;
    }
    return false;
}

void TtsPlaybackSession::RememberFinalLocked(const TtsFinalAck& ack) {
    final_acks_.push_front(ack);
    while (final_acks_.size() > kAckHistorySize) final_acks_.pop_back();
}

void TtsPlaybackSession::RememberStaleLocked(const std::string& sentence_id) {
    if (sentence_id.empty()) return;
    stale_sentence_ids_.push_front(sentence_id);
    while (stale_sentence_ids_.size() > kAckHistorySize) stale_sentence_ids_.pop_back();
}
```

- [ ] **Step 6: Wire the source into the main component and path test**

Add `audio/tts_playback_session.cc` beside `audio/audio_service.cc` in `main/CMakeLists.txt`, and append:

```python
def test_tts_playback_session_is_compiled_into_firmware():
    cmake = Path("main/CMakeLists.txt").read_text(encoding="utf-8")
    assert "audio/tts_playback_session.cc" in cmake
```

- [ ] **Step 7: Compile and run the host state-machine test**

```powershell
g++ -std=c++17 -I tests/stubs -I main/audio tests/tts_playback_session_test.cc main/audio/tts_playback_session.cc -o build/host-tests/tts_playback_session_test.exe
& build/host-tests/tts_playback_session_test.exe
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py -q
```

Expected: executable exits `0`; pytest passes.

- [ ] **Step 8: Commit the isolated session module**

```powershell
git add main/audio/tts_playback_session.h main/audio/tts_playback_session.cc main/CMakeLists.txt tests/stubs/protocol.h tests/tts_playback_session_test.cc tests/xiaoxin_tts_reliable_playback_path_test.py
git commit -m "feat: add ordered tts playback session"
```

---

### Task 4: Firmware lossless decode admission and hardware-timeline drain fence

**Files:**
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\audio\audio_service.h:80-199`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\audio\audio_service.cc:293-403,529-540,679-703`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_tts_reliable_playback_path_test.py`

**Interfaces:**
- Produces: `AudioServiceCallbacks::on_decode_queue_available`.
- Produces: `bool AudioService::TryPushPacketToDecodeQueue(std::unique_ptr<AudioStreamPacket>& packet)`; false preserves caller ownership.
- Produces: `bool AudioService::WaitForPlaybackDrained(std::chrono::milliseconds timeout)`.
- Keeps existing `PushPacketToDecodeQueue()` for ordinary audio callers.
- Consumed by: Task 5 session pump and drain task.

- [ ] **Step 1: Add failing source-path tests for lossless admission and drain semantics**

Append:

```python
AUDIO_SERVICE_H = Path("main/audio/audio_service.h")
AUDIO_SERVICE_CC = Path("main/audio/audio_service.cc")


def test_audio_service_exposes_lossless_decode_admission():
    header = read(AUDIO_SERVICE_H)
    source = read(AUDIO_SERVICE_CC)
    assert "bool TryPushPacketToDecodeQueue(std::unique_ptr<AudioStreamPacket>& packet);" in header
    assert "audio_decode_queue_.push_back(std::move(packet));" in source
    assert "callbacks_.on_decode_queue_available" in source


def test_playback_drain_tracks_active_output_and_expected_speaker_time():
    header = read(AUDIO_SERVICE_H)
    source = read(AUDIO_SERVICE_CC)
    assert "bool WaitForPlaybackDrained(std::chrono::milliseconds timeout);" in header
    assert "bool audio_output_busy_ = false;" in header
    assert "last_output_expected_end_" in header
    assert "audio_output_sequence_" in header
    assert "audio_output_busy_ = true;" in source
    assert "audio_output_busy_ = false;" in source
    assert "last_output_expected_end_ =" in source
```

- [ ] **Step 2: Verify the new tests fail**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py -q
```

Expected: failures for the new callback, method and drain fields.

- [ ] **Step 3: Extend the audio service interface and state**

Add `#include <algorithm>` to `audio_service.cc` for `std::min`/`std::max`.

Add to `AudioServiceCallbacks`:

```cpp
std::function<void(void)> on_decode_queue_available;
```

Add public methods:

```cpp
bool TryPushPacketToDecodeQueue(std::unique_ptr<AudioStreamPacket>& packet);
bool WaitForPlaybackDrained(std::chrono::milliseconds timeout);
```

Add private state:

```cpp
bool audio_output_busy_ = false;
uint64_t audio_output_sequence_ = 0;
std::chrono::steady_clock::time_point last_output_expected_end_ =
    std::chrono::steady_clock::now();
```

- [ ] **Step 4: Make decode admission preserve packets when the queue is full**

Add:

```cpp
bool AudioService::TryPushPacketToDecodeQueue(
    std::unique_ptr<AudioStreamPacket>& packet) {
    std::lock_guard<std::mutex> lock(audio_queue_mutex_);
    if (!packet || audio_decode_queue_.size() >= MAX_DECODE_PACKETS_IN_QUEUE) {
        return false;
    }
    audio_decode_queue_.push_back(std::move(packet));
    audio_queue_cv_.notify_all();
    return true;
}
```

Keep the old method by delegating only for non-waiting callers and retaining its blocking behavior for `wait=true`:

```cpp
bool AudioService::PushPacketToDecodeQueue(
    std::unique_ptr<AudioStreamPacket> packet, bool wait) {
    if (!wait) {
        return TryPushPacketToDecodeQueue(packet);
    }
    std::unique_lock<std::mutex> lock(audio_queue_mutex_);
    audio_queue_cv_.wait(lock, [this]() {
        return service_stopped_ ||
               audio_decode_queue_.size() < MAX_DECODE_PACKETS_IN_QUEUE;
    });
    if (service_stopped_) return false;
    audio_decode_queue_.push_back(std::move(packet));
    audio_queue_cv_.notify_all();
    return true;
}
```

- [ ] **Step 5: Notify the application whenever decode capacity opens**

Immediately after popping a decode packet and unlocking in `OpusCodecTask()`:

```cpp
lock.unlock();
if (callbacks_.on_decode_queue_available) {
    callbacks_.on_decode_queue_available();
}
```

Do not invoke the callback while `audio_queue_mutex_` is held.

- [ ] **Step 6: Track active output and the expected final-sample time**

When `AudioOutputTask()` pops a playback task, set busy before unlocking:

```cpp
auto task = std::move(audio_playback_queue_.front());
audio_playback_queue_.pop_front();
audio_output_busy_ = true;
audio_queue_cv_.notify_all();
lock.unlock();
```

Replace the post-`OutputData()` timestamp update with:

```cpp
codec_->OutputData(task->pcm);

const auto now = std::chrono::steady_clock::now();
const int channels = std::max(codec_->output_channels(), 1);
const int sample_rate = std::max(codec_->output_sample_rate(), 1);
const auto duration_us = std::chrono::microseconds(
    static_cast<int64_t>(task->pcm.size()) * 1000000LL /
    (static_cast<int64_t>(sample_rate) * channels));

lock.lock();
const auto queued_from = std::max(last_output_expected_end_, now);
last_output_expected_end_ = queued_from + duration_us;
last_output_time_ = now;
audio_output_busy_ = false;
++audio_output_sequence_;
audio_queue_cv_.notify_all();
lock.unlock();
debug_statistics_.playback_count++;
```

- [ ] **Step 7: Implement a rechecking drain fence**

```cpp
bool AudioService::WaitForPlaybackDrained(std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    std::unique_lock<std::mutex> lock(audio_queue_mutex_);
    while (!service_stopped_) {
        const bool software_empty =
            audio_decode_queue_.empty() &&
            audio_playback_queue_.empty() &&
            !audio_output_busy_;
        const auto now = std::chrono::steady_clock::now();
        const auto speaker_end =
            last_output_expected_end_ + std::chrono::milliseconds(20);
        if (software_empty && now >= speaker_end) {
            return true;
        }
        if (now >= deadline) return false;

        const auto wake_at = software_empty
            ? std::min(speaker_end, deadline)
            : deadline;
        audio_queue_cv_.wait_until(lock, wake_at);
    }
    return false;
}
```

Keep `WaitForPlaybackQueueEmpty()` for existing callers, but do not use it for reliable TTS done.

- [ ] **Step 8: Run focused audio tests**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py tests/xiaoxin_microphone_slot_path_test.py -q
```

Expected: selected tests pass.

- [ ] **Step 9: Commit the audio drain slice**

```powershell
git add main/audio/audio_service.h main/audio/audio_service.cc tests/xiaoxin_tts_reliable_playback_path_test.py
git commit -m "feat: add reliable audio playback drain fence"
```

---

### Task 5: Firmware Application integration for PREPARING, pre-roll, ready, error and done

**Files:**
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\application.h:21-188`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\application.cc:133-151,242-345,792-857,1260-1313`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_tts_reliable_playback_path_test.py`
- Modify: `D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\xiaoxin_voice_state_flow_path_test.py`

**Interfaces:**
- Consumes: `Board::PrepareForAudioPlayback()`, `Protocol::SendTtsAck()`, `TtsPlaybackSession`, `AudioService::TryPushPacketToDecodeQueue()`, `WaitForPlaybackDrained()`.
- Produces internal methods: `HandleReliableTtsStart`, `PrepareReliableTts`, `HandleTtsAudioPump`, `HandleReliableTtsStop`, `RunTtsDrain`, `FailReliableTts`.
- Produces event bit: `MAIN_EVENT_TTS_AUDIO_PUMP`.

- [ ] **Step 1: Add failing integration path tests**

Append tests that extract `Application::InitializeProtocol()` and assert these exact contracts:

```python
APPLICATION_H = Path("main/application.h")
APPLICATION_CC = Path("main/application.cc")


def test_application_enters_preparing_before_scheduling_heavy_work():
    source = read(APPLICATION_CC)
    start = source.index("void Application::HandleReliableTtsStart")
    end = source.index("void Application::PrepareReliableTts", start)
    body = source[start:end]
    assert "tts_playback_session_.Start(sentence_id)" in body
    assert "Schedule(" in body
    assert body.index("tts_playback_session_.Start(sentence_id)") < body.index("Schedule(")


def test_incoming_audio_is_owned_by_ordered_tts_ingress():
    source = read(APPLICATION_CC)
    callback = source[source.index("protocol_->OnIncomingAudio"):source.index("protocol_->OnAudioChannelOpened")]
    assert "tts_playback_session_.Enqueue(std::move(packet))" in callback
    assert "GetDeviceState() == kDeviceStateSpeaking" not in callback
    assert "preroll_overflow" in callback


def test_ready_is_after_screen_wake_reset_and_pump_activation():
    source = read(APPLICATION_CC)
    start = source.index("void Application::PrepareReliableTts")
    end = source.index("void Application::HandleTtsAudioPump", start)
    body = source[start:end]
    for call in (
        "Board::GetInstance().PrepareForAudioPlayback();",
        "audio_service_.ResetDecoder();",
        "audio_service_.WaitForPlaybackDrained(std::chrono::milliseconds(500))",
        "tts_playback_session_.MarkPlaying(generation)",
        "HandleTtsAudioPump();",
        'protocol_->SendTtsAck("ready", sentence_id);',
    ):
        assert call in body
    assert body.index("PrepareForAudioPlayback") < body.index("ResetDecoder")
    assert body.index("ResetDecoder") < body.index("WaitForPlaybackDrained")
    assert body.index("WaitForPlaybackDrained") < body.index("MarkPlaying")
    assert body.index("MarkPlaying") < body.index("SendTtsAck")


def test_done_requires_ingress_and_audio_drain_before_ack():
    source = read(APPLICATION_CC)
    start = source.index("void Application::RunTtsDrain")
    end = source.index("void Application::FailReliableTts", start)
    body = source[start:end]
    assert "WaitForIngressEmpty" in body
    assert "WaitForPlaybackDrained" in body
    assert 'SendTtsAck("done", sentence_id)' in body
    assert body.index("WaitForIngressEmpty") < body.index("WaitForPlaybackDrained")
    assert body.index("WaitForPlaybackDrained") < body.index('SendTtsAck("done"')


def test_xiaoxin_event_card_is_idempotent_by_delivery_id():
    source = read(APPLICATION_CC)
    start = source.index("void Application::HandleXiaoxinEvent")
    end = source.index("void Application::HandleXiaoxinOverviewUpdate", start)
    body = source[start:end]
    assert 'std::string notification_id = std::string("xiaoxin_event:") + delivery_id;' in body
    assert 'protocol_->SendXiaoxinAck(delivery_id, "device_received");' in body
```

- [ ] **Step 2: Run integration tests and verify failure**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py tests/xiaoxin_voice_state_flow_path_test.py -q
```

Expected: new reliable TTS tests fail; existing voice state tests remain useful regression signals.

- [ ] **Step 3: Add state, event bit and private method declarations**

In `application.h` add:

```cpp
#include "audio/tts_playback_session.h"

#define MAIN_EVENT_TTS_AUDIO_PUMP        (1 << 14)
```

Add private state and declarations:

```cpp
TtsPlaybackSession tts_playback_session_;
int64_t tts_prepare_started_us_ = 0;

void HandleReliableTtsStart(const std::string& sentence_id);
void PrepareReliableTts(uint32_t generation, const std::string& sentence_id);
void HandleTtsAudioPump();
void HandleReliableTtsStop(const std::string& sentence_id);
void RunTtsDrain(uint32_t generation, const std::string& sentence_id);
void FailReliableTts(uint32_t generation,
                     const std::string& sentence_id,
                     const std::string& reason);
```

- [ ] **Step 4: Wire decode capacity and the main-task pump event**

During `AudioServiceCallbacks` initialization:

```cpp
callbacks.on_decode_queue_available = [this]() {
    xEventGroupSetBits(event_group_, MAIN_EVENT_TTS_AUDIO_PUMP);
};
```

Add the bit to `ALL_EVENTS` and handle it before scheduled callbacks:

```cpp
if (bits & MAIN_EVENT_TTS_AUDIO_PUMP) {
    HandleTtsAudioPump();
}
```

- [ ] **Step 5: Route every incoming downlink packet through the session**

Replace the Speaking-only callback with:

```cpp
protocol_->OnIncomingAudio([this](std::unique_ptr<AudioStreamPacket> packet) {
    const auto result = tts_playback_session_.Enqueue(std::move(packet));
    if (result == TtsIngressResult::kAccepted) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_TTS_AUDIO_PUMP);
        return;
    }
    if (result == TtsIngressResult::kOverflow) {
        const uint32_t generation = tts_playback_session_.generation();
        const std::string sentence_id = tts_playback_session_.sentence_id();
        Schedule([this, generation, sentence_id]() {
            FailReliableTts(generation, sentence_id, "preroll_overflow");
        });
    }
});
```

For audio without a reliable sentence, retain legacy behavior by adding this first branch:

```cpp
if (tts_playback_session_.phase() == TtsPlaybackPhase::kIdle) {
    if (GetDeviceState() == kDeviceStateSpeaking) {
        audio_service_.PushPacketToDecodeQueue(std::move(packet));
    }
    return;
}
```

- [ ] **Step 6: Parse correlated start/stop while preserving the legacy no-ID path**

Inside the `type=tts` branch, read a validated sentence id:

```cpp
auto sentence = cJSON_GetObjectItem(root, "sentence_id");
const bool has_sentence_id = cJSON_IsString(sentence) && sentence->valuestring[0] != '\0';
const std::string sentence_id = has_sentence_id ? sentence->valuestring : "";
```

Replace the start/stop branches with:

```cpp
if (strcmp(state->valuestring, "start") == 0) {
    if (has_sentence_id) {
        HandleReliableTtsStart(sentence_id);
    } else {
        Schedule([this]() {
            aborted_ = false;
            SetDeviceState(kDeviceStateSpeaking);
        });
    }
} else if (strcmp(state->valuestring, "stop") == 0) {
    if (has_sentence_id) {
        HandleReliableTtsStop(sentence_id);
    } else {
        Schedule([this]() {
            if (GetDeviceState() != kDeviceStateSpeaking) return;
            if (listening_mode_ == kListeningModeManualStop) {
                SetDeviceState(kDeviceStateIdle);
            } else {
                SetDeviceState(kDeviceStateListening);
            }
        });
    }
}
```

- [ ] **Step 7: Implement synchronous start idempotency and asynchronous preparation**

```cpp
void Application::HandleReliableTtsStart(const std::string& sentence_id) {
    const TtsStartDecision decision = tts_playback_session_.Start(sentence_id);
    if (!decision.superseded_sentence_id.empty() && protocol_) {
        protocol_->SendTtsAck("error", decision.superseded_sentence_id, "superseded");
        audio_service_.ResetDecoder();
    }
    switch (decision.action) {
        case TtsStartAction::kPrepare:
            tts_prepare_started_us_ = esp_timer_get_time();
            Schedule([this, generation = decision.generation, sentence_id]() {
                PrepareReliableTts(generation, sentence_id);
            });
            break;
        case TtsStartAction::kResendReady:
            if (protocol_) protocol_->SendTtsAck("ready", sentence_id);
            break;
        case TtsStartAction::kReplayFinal:
            if (protocol_) {
                protocol_->SendTtsAck(
                    decision.final_ack.state,
                    decision.final_ack.sentence_id,
                    decision.final_ack.reason);
            }
            break;
        case TtsStartAction::kRejectStale:
            if (protocol_) protocol_->SendTtsAck("error", sentence_id, "stale_start");
            break;
        case TtsStartAction::kContinuePreparing:
        case TtsStartAction::kContinueDraining:
            break;
    }
}

void Application::PrepareReliableTts(
    uint32_t generation, const std::string& sentence_id) {
    if (!tts_playback_session_.IsCurrent(generation, sentence_id)) return;
    const int64_t wake_started_us = esp_timer_get_time();
    Board::GetInstance().PrepareForAudioPlayback();
    const int64_t screen_wake_ms =
        (esp_timer_get_time() - wake_started_us) / 1000;
    aborted_ = false;
    audio_service_.EnableVoiceProcessing(false);
    audio_service_.EnableWakeWordDetection(false);
    audio_service_.ResetDecoder();
    if (!audio_service_.WaitForPlaybackDrained(std::chrono::milliseconds(500))) {
        FailReliableTts(generation, sentence_id, "pipeline_reset_timeout");
        return;
    }
    if (GetDeviceState() != kDeviceStateSpeaking) {
        SetDeviceState(kDeviceStateSpeaking);
    }
    if (!tts_playback_session_.MarkPlaying(generation)) return;
    const size_t preroll_packets = tts_playback_session_.buffered_packets();
    HandleTtsAudioPump();
    if (protocol_ && tts_playback_session_.IsCurrent(generation, sentence_id)) {
        ESP_LOGI(
            TAG,
            "tts_state=ready sentence_id=%s generation=%lu screen_wake_ms=%lld start_to_ready_ms=%lld preroll_packets=%u",
            sentence_id.c_str(),
            static_cast<unsigned long>(generation),
            static_cast<long long>(screen_wake_ms),
            static_cast<long long>((esp_timer_get_time() - tts_prepare_started_us_) / 1000),
            static_cast<unsigned>(preroll_packets));
        protocol_->SendTtsAck("ready", sentence_id);
    }
}
```

In `OnStateChanged(kDeviceStateSpeaking)`, retain display/wake-word behavior but guard the existing decoder reset:

```cpp
if (!tts_playback_session_.OwnsPlaybackPipeline()) {
    audio_service_.ResetDecoder();
}
```

- [ ] **Step 8: Implement the serial pump**

```cpp
void Application::HandleTtsAudioPump() {
    tts_playback_session_.Pump(
        [this](std::unique_ptr<AudioStreamPacket>& packet) {
            return audio_service_.TryPushPacketToDecodeQueue(packet);
        });
}
```

- [ ] **Step 9: Implement stop, background drain and final ACK ordering**

```cpp
void Application::HandleReliableTtsStop(const std::string& sentence_id) {
    const uint32_t generation = tts_playback_session_.generation();
    if (!tts_playback_session_.BeginDraining(sentence_id, generation)) {
        const TtsFinalAck final_ack =
            tts_playback_session_.FinalAckFor(sentence_id);
        if (protocol_ && !final_ack.state.empty()) {
            protocol_->SendTtsAck(
                final_ack.state, final_ack.sentence_id, final_ack.reason);
        }
        return;
    }
    xEventGroupSetBits(event_group_, MAIN_EVENT_TTS_AUDIO_PUMP);
    struct DrainContext {
        Application* app;
        uint32_t generation;
        std::string sentence_id;
    };
    auto* context = new DrainContext{this, generation, sentence_id};
    const BaseType_t created = xTaskCreate([](void* arg) {
        std::unique_ptr<DrainContext> context(static_cast<DrainContext*>(arg));
        context->app->RunTtsDrain(context->generation, context->sentence_id);
        vTaskDelete(nullptr);
    }, "tts_drain", 4096, context, 4, nullptr);
    if (created != pdPASS) {
        delete context;
        Schedule([this, generation, sentence_id]() {
            FailReliableTts(generation, sentence_id, "drain_task_create_failed");
        });
    }
}

void Application::RunTtsDrain(
    uint32_t generation, const std::string& sentence_id) {
    constexpr auto kDrainTimeout = std::chrono::milliseconds(8000);
    const auto started = std::chrono::steady_clock::now();
    const bool ingress_empty =
        tts_playback_session_.WaitForIngressEmpty(generation, kDrainTimeout);
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - started);
    const auto remaining = elapsed < kDrainTimeout
        ? kDrainTimeout - elapsed
        : std::chrono::milliseconds(0);
    const bool audio_empty = ingress_empty &&
        audio_service_.WaitForPlaybackDrained(remaining);
    const int64_t done_wait_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - started).count();
    Schedule([this, generation, sentence_id, audio_empty, done_wait_ms]() {
        if (!tts_playback_session_.IsCurrent(generation, sentence_id)) return;
        if (!audio_empty) {
            FailReliableTts(generation, sentence_id, "playback_drain_timeout");
            return;
        }
        ESP_LOGI(
            TAG,
            "tts_state=done sentence_id=%s generation=%lu done_wait_ms=%lld",
            sentence_id.c_str(),
            static_cast<unsigned long>(generation),
            static_cast<long long>(done_wait_ms));
        if (!tts_playback_session_.Complete(generation, "done", "")) return;
        if (protocol_) protocol_->SendTtsAck("done", sentence_id);
        if (listening_mode_ == kListeningModeManualStop) {
            SetDeviceState(kDeviceStateIdle);
        } else {
            SetDeviceState(kDeviceStateListening);
        }
    });
}

void Application::FailReliableTts(
    uint32_t generation,
    const std::string& sentence_id,
    const std::string& reason) {
    if (!tts_playback_session_.IsCurrent(generation, sentence_id)) return;
    ESP_LOGW(
        TAG,
        "tts_state=error sentence_id=%s generation=%lu failure_reason=%s preroll_packets=%u",
        sentence_id.c_str(),
        static_cast<unsigned long>(generation),
        reason.c_str(),
        static_cast<unsigned>(tts_playback_session_.buffered_packets()));
    audio_service_.ResetDecoder();
    if (!tts_playback_session_.Fail(generation, reason)) return;
    if (protocol_) protocol_->SendTtsAck("error", sentence_id, reason);
    SetDeviceState(kDeviceStateIdle);
}
```

- [ ] **Step 10: Abort the current generation on connection close and user interruption**

In audio-channel close, network disconnect and `AbortSpeaking()` paths, call:

```cpp
tts_playback_session_.AbortCurrent("connection_closed");
audio_service_.ResetDecoder();
```

Use `"interrupted"` instead when the user explicitly interrupts. Do not send done for the aborted generation.

In `HandleXiaoxinEvent`, make repeated delivery of the same card idempotent without merging different reminders of the same event type:

```cpp
std::string notification_id = std::string("xiaoxin_event:") + delivery_id;
```

Keep `SendXiaoxinAck(delivery_id, "device_received")` after the display update is scheduled.

- [ ] **Step 11: Run the firmware integration suite**

```powershell
python -m pytest tests/xiaoxin_tts_reliable_playback_path_test.py tests/xiaoxin_voice_state_flow_path_test.py tests/xiaoxin_low_power_clock_visual_path_test.py tests/xiaoxin_protocol_compatibility_test.py -q
g++ -std=c++17 -I tests/stubs -I main/audio tests/tts_playback_session_test.cc main/audio/tts_playback_session.cc -o build/host-tests/tts_playback_session_test.exe
& build/host-tests/tts_playback_session_test.exe
```

Expected: all selected tests and the host executable pass.

- [ ] **Step 12: Build the configured Waveshare ESP32-S3 firmware**

Open an ESP-IDF-enabled PowerShell, verify `CONFIG_BOARD_TYPE_WAVESHARE_ESP32_S3_TOUCH_LCD_1_46=y`, then run:

```powershell
idf.py build
```

Expected: build exits `0`. If `idf.py` is not on PATH, load the installed ESP-IDF export script first; do not change `sdkconfig` or create a different board build.

- [ ] **Step 13: Commit the firmware orchestration slice**

```powershell
git add main/application.h main/application.cc tests/xiaoxin_tts_reliable_playback_path_test.py tests/xiaoxin_voice_state_flow_path_test.py
git commit -m "feat: coordinate reliable tts playback on device"
```

---

### Task 6: Server typed ACK results, error ACK handling and session correlation

**Files:**
- Create: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\xiaoxin\tts_delivery.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\connection.py:91-175,1545-1603`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\handle\textHandler\ttsMessageHandler.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_tts_playback_ack.py`

**Interfaces:**
- Produces: immutable `TtsAckResult`, `TtsAttemptError`, `TtsAttemptOutcome`.
- Changes `wait_for_tts_ack()` return type from `bool` to `TtsAckResult | None`.
- Produces `resolve_tts_error(sentence_id, reason)` that resolves the active ready or done waiter for the same connection object.
- Consumed by: Tasks 7 and 8.

- [ ] **Step 1: Write failing ACK result tests**

Add:

```python
def test_reliable_tts_requires_all_three_features():
    conn = make_conn(
        features={
            "tts_ready_ack": True,
            "tts_done_ack": True,
            "tts_preroll_buffer": True,
        }
    )
    assert conn.supports_reliable_notification_tts() is True
    conn.features.pop("tts_preroll_buffer")
    assert conn.supports_reliable_notification_tts() is False


def test_wait_for_tts_ack_returns_typed_success():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        conn.resolve_tts_ack("ready", "sentence-1")
        return await conn.wait_for_tts_ack("ready", "sentence-1", 10)

    result = asyncio.run(scenario())
    assert result.state == "ready"
    assert result.sentence_id == "sentence-1"
    assert result.reason is None


def test_error_ack_resolves_current_waiter_with_reason():
    async def scenario():
        conn = make_conn()
        conn.begin_tts_ack_wait("ready", "sentence-1")
        assert conn.resolve_tts_error("sentence-1", "preroll_overflow") is True
        return await conn.wait_for_tts_ack("ready", "sentence-1", 10)

    result = asyncio.run(scenario())
    assert result.state == "error"
    assert result.reason == "preroll_overflow"


def test_tts_handler_rejects_wrong_session_id():
    async def scenario():
        conn = make_conn()
        conn.session_id = "current-session"
        waiter = conn.begin_tts_ack_wait("done", "sentence-1")
        await TtsTextMessageHandler().handle(
            conn,
            {
                "type": "tts",
                "state": "done",
                "session_id": "old-session",
                "sentence_id": "sentence-1",
            },
        )
        return waiter.done()

    assert asyncio.run(scenario()) is False
```

- [ ] **Step 2: Run tests and verify failure**

Run from `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server`:

```powershell
python -m pytest tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: new typed-result and error/session tests fail.

- [ ] **Step 3: Create the shared TTS delivery types**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TtsAckResult:
    state: str
    sentence_id: str
    reason: str | None = None

    @property
    def successful(self) -> bool:
        return self.state in {"ready", "done"} and self.reason is None


class TtsAttemptError(RuntimeError):
    def __init__(self, sentence_id: str, reason: str):
        super().__init__(f"tts attempt failed: {reason}")
        self.sentence_id = sentence_id
        self.reason = reason


@dataclass(frozen=True)
class TtsAttemptOutcome:
    sentence_id: str
    status: str
    reason: str | None = None
```

- [ ] **Step 4: Replace Event waiters with typed Future waiters**

In `ConnectionHandler.__init__` keep the same dictionaries but define their values as futures/results. Implement:

```python
def supports_tts_preroll_buffer(self) -> bool:
    return bool((self.features or {}).get("tts_preroll_buffer"))

def supports_reliable_notification_tts(self) -> bool:
    return (
        self.supports_tts_ready_ack()
        and self.supports_tts_done_ack()
        and self.supports_tts_preroll_buffer()
    )

def begin_tts_ack_wait(
    self, state: str, sentence_id: str
) -> asyncio.Future[TtsAckResult]:
    self._prune_tts_completed_acks()
    key = self._tts_ack_key(state, sentence_id)
    future = self.tts_ack_waiters.get(key)
    if future is None or future.done():
        future = asyncio.get_running_loop().create_future()
        completed = self.tts_ack_completed.get(key)
        if completed is not None:
            future.set_result(completed[0])
        self.tts_ack_waiters[key] = future
    return future

def resolve_tts_ack(self, state: str, sentence_id: str) -> bool:
    result = TtsAckResult(state=state, sentence_id=sentence_id)
    key = self._tts_ack_key(state, sentence_id)
    future = self.tts_ack_waiters.get(key)
    self.tts_ack_completed[key] = (result, time.monotonic())
    if future is None or future.done():
        return False
    future.set_result(result)
    return True

def resolve_tts_error(self, sentence_id: str, reason: str) -> bool:
    for state in ("ready", "done"):
        key = self._tts_ack_key(state, sentence_id)
        future = self.tts_ack_waiters.get(key)
        if future is not None and not future.done():
            result = TtsAckResult("error", sentence_id, reason)
            self.tts_ack_completed[key] = (result, time.monotonic())
            future.set_result(result)
            return True
    return False

async def wait_for_tts_ack(
    self, state: str, sentence_id: str, timeout_ms: int
) -> TtsAckResult | None:
    key = self._tts_ack_key(state, sentence_id)
    completed = self.tts_ack_completed.pop(key, None)
    if completed is not None:
        self.tts_ack_waiters.pop(key, None)
        return completed[0]
    future = self.tts_ack_waiters.get(key) or self.begin_tts_ack_wait(state, sentence_id)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout_ms / 1000)
    except asyncio.TimeoutError:
        return None
    finally:
        self.tts_ack_waiters.pop(key, None)
        self.tts_ack_completed.pop(key, None)
```

Update `_prune_tts_completed_acks()` to read `(result, completed_at)` tuples.

```python
def _prune_tts_completed_acks(self) -> None:
    stale_before = time.monotonic() - self.tts_ack_completed_ttl_seconds
    for key, (_, completed_at) in list(self.tts_ack_completed.items()):
        if completed_at < stale_before:
            self.tts_ack_completed.pop(key, None)
```

- [ ] **Step 5: Handle ready, done and error with session validation**

Replace the handler body with:

```python
state = msg_json.get("state")
if state not in {"ready", "done", "error"}:
    conn.logger.bind(tag=TAG).debug(f"Ignoring device tts state: {state}")
    return

if msg_json.get("session_id") != conn.session_id:
    conn.logger.bind(tag=TAG).warning(
        f"Ignoring tts {state} ack from stale session"
    )
    return

sentence_id = msg_json.get("sentence_id")
if not isinstance(sentence_id, str) or not sentence_id:
    conn.logger.bind(tag=TAG).warning(
        f"Ignoring tts {state} ack with invalid sentence_id"
    )
    return

if state == "error":
    reason = msg_json.get("reason")
    if not isinstance(reason, str) or not reason:
        reason = "unknown_device_error"
    resolved = conn.resolve_tts_error(sentence_id, reason)
    if hasattr(conn, "mark_xiaoxin_control_tts_failed"):
        conn.mark_xiaoxin_control_tts_failed(sentence_id, reason)
else:
    resolved = conn.resolve_tts_ack(state, sentence_id)

if not resolved:
    conn.logger.bind(tag=TAG).debug(
        f"Ignoring unmatched tts {state} ack for sentence_id={sentence_id}"
    )
```

- [ ] **Step 6: Update old bool assertions and run ACK tests**

Update existing tests so timeout expects `None`, success inspects `.successful`, and every ACK payload uses `conn.session_id` rather than an arbitrary session string.

```powershell
python -m pytest tests/xiaoxin/test_tts_playback_ack.py tests/xiaoxin/test_connection_ack.py -q
```

Expected: both files pass.

- [ ] **Step 7: Commit the typed ACK slice**

```powershell
git add main/xiaozhi-server/core/xiaoxin/tts_delivery.py main/xiaozhi-server/core/connection.py main/xiaozhi-server/core/handle/textHandler/ttsMessageHandler.py main/xiaozhi-server/tests/xiaoxin/test_tts_playback_ack.py main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py
git commit -m "feat: correlate reliable tts acknowledgements"
```

---

### Task 7: Server start handshake, full-text attempt creation and done result enforcement

**Files:**
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\connection.py:1787-1844`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\handle\sendAudioHandle.py:267-338`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_connection_ack.py:272-647`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_connection_integration.py:365-386`

**Interfaces:**
- Changes: `speak_from_control_console(text: str, delivery_id: str, sentence_id: str) -> None`; dispatcher owns each new `sentence_id`.
- Produces: `mark_xiaoxin_control_tts_failed(sentence_id, reason)` and `mark_xiaoxin_control_tts_legacy_unverified(sentence_id)` callbacks.
- Enforces four start sends maximum per attempt: initial plus 300/600/1200 ms retries, all with the same sentence id.
- Enforces: no TTS queue submission before successful ready in reliable mode; done timeout/error never calls `mark_tts_done`.
- Consumed by: Task 8 dispatcher attempt loop.

- [ ] **Step 1: Write failing ready retry and no-partial-audio tests**

Add to `test_connection_ack.py`:

```python
def test_control_console_ready_timeout_retries_same_sentence_then_raises(monkeypatch):
    events = []
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_ready_ack_timeout_ms"] = 700
    conn.config["tts_ready_start_retry_delays_ms"] = [300, 600, 1200]
    conn.tts = SimpleNamespace(
        tts_text_queue=queue.Queue(),
        store_tts_text=lambda sentence_id, text: events.append((sentence_id, text)),
    )

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        events.append((state, sentence_id))

    async def fake_wait(state, sentence_id, timeout_ms):
        events.append(("wait", sentence_id, timeout_ms))
        return None

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)
    conn.wait_for_tts_ack = fake_wait

    with pytest.raises(TtsAttemptError) as exc:
        asyncio.run(
            conn.speak_from_control_console(
                "完整提醒文本", "delivery-1", "sentence-fixed"
            )
        )

    assert exc.value.reason == "ready_timeout"
    assert [event for event in events if event[0] == "start"] == [
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
    ]
    assert list(conn.tts.tts_text_queue.queue) == []
    assert not any(isinstance(event, tuple) and len(event) == 2 and event[0] == "sentence-fixed" for event in events)
```

Add the complete success-after-retry test:

```python
def test_control_console_queues_full_text_once_after_ready_retry(monkeypatch):
    events = []
    conn = _make_connection_handler()
    conn.websocket = FakeWebSocket()
    conn.features = {
        "tts_ready_ack": True,
        "tts_done_ack": True,
        "tts_preroll_buffer": True,
    }
    conn.config["tts_ready_start_retry_delays_ms"] = [300, 600, 1200]

    class FakeTts:
        def __init__(self):
            self.tts_text_queue = queue.Queue()
            self.stored = []

        def store_tts_text(self, sentence_id, text):
            self.stored.append((sentence_id, text))

    conn.tts = FakeTts()
    waits = 0

    async def fake_send(conn_arg, state, text=None, sentence_id=None):
        events.append((state, sentence_id))

    async def fake_wait(state, sentence_id, timeout_ms):
        nonlocal waits
        waits += 1
        if waits < 3:
            return None
        return TtsAckResult("ready", sentence_id)

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("core.connection.send_tts_message", fake_send)
    monkeypatch.setattr("core.connection.asyncio.sleep", fake_sleep)
    conn.wait_for_tts_ack = fake_wait

    asyncio.run(
        conn.speak_from_control_console(
            "完整提醒文本", "delivery-1", "sentence-fixed"
        )
    )

    queued = list(conn.tts.tts_text_queue.queue)
    assert [item.sentence_type for item in queued] == [
        SentenceType.FIRST,
        SentenceType.MIDDLE,
        SentenceType.LAST,
    ]
    assert queued[1].content_detail == "完整提醒文本"
    assert conn.tts.stored == [("sentence-fixed", "完整提醒文本")]
    assert events == [
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
        ("start", "sentence-fixed"),
    ]
```

- [ ] **Step 2: Write failing done timeout/error tests**

```python
def test_reliable_control_stop_does_not_complete_when_done_times_out(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}
    failures = []
    conn.supports_reliable_notification_tts = lambda: True
    conn.wait_for_tts_ack = lambda *args: asyncio.sleep(0, result=None)
    conn.mark_xiaoxin_control_tts_failed = (
        lambda sentence_id, reason: failures.append((sentence_id, reason))
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert failures == [("sentence-1", "done_timeout")]
    assert conn.done_calls == []
```

Add the symmetric success test:

```python
def test_reliable_control_stop_completes_only_after_matching_done(monkeypatch):
    conn = FakeConn()
    conn.sentence_id = "sentence-1"
    conn.xiaoxin_control_tts_deliveries = {"sentence-1": "delivery-1"}
    completed = []
    failed = []
    conn.supports_reliable_notification_tts = lambda: True
    conn.wait_for_tts_ack = lambda *args: asyncio.sleep(
        0, result=TtsAckResult("done", "sentence-1")
    )
    conn.mark_xiaoxin_control_tts_done = completed.append
    conn.mark_xiaoxin_control_tts_failed = (
        lambda sentence_id, reason: failed.append((sentence_id, reason))
    )
    monkeypatch.setattr(
        "core.handle.sendAudioHandle._wait_for_audio_completion",
        lambda conn_arg: asyncio.sleep(0),
    )

    asyncio.run(send_tts_message(conn, "stop", sentence_id="sentence-1"))

    assert completed == ["sentence-1"]
    assert failed == []
```

Add these imports at the top of the test file:

```python
import pytest
from core.xiaoxin.tts_delivery import TtsAckResult, TtsAttemptError
```

- [ ] **Step 3: Run focused tests and verify failure**

```powershell
python -m pytest tests/xiaoxin/test_connection_ack.py tests/xiaoxin/test_connection_integration.py -q
```

Expected: new signature, retry and done-enforcement tests fail.

- [ ] **Step 4: Make dispatcher-owned sentence ids explicit in the connection API**

Change the method signature and remove internal UUID creation:

```python
async def speak_from_control_console(
    self, text: str, delivery_id: str, sentence_id: str
) -> None:
    if not text:
        raise TTSException("control console TTS text is empty")
    if not sentence_id:
        raise TTSException("control console TTS sentence_id is empty")
    await self._wait_until_tts_ready(timeout_seconds=5)
    self.sentence_id = sentence_id
    self.xiaoxin_control_tts_deliveries[sentence_id] = delivery_id
```

- [ ] **Step 5: Implement the reliable ready loop before submitting text**

```python
if self.supports_reliable_notification_tts():
    timeout_ms = int(self.config.get("tts_ready_ack_timeout_ms", 700))
    retry_delays_ms = list(
        self.config.get("tts_ready_start_retry_delays_ms", [300, 600, 1200])
    )
    ready = False
    for send_index in range(len(retry_delays_ms) + 1):
        self.begin_tts_ack_wait("ready", sentence_id)
        await send_tts_message(self, "start", sentence_id=sentence_id)
        self.client_is_speaking = True
        result = await self.wait_for_tts_ack(
            "ready", sentence_id, timeout_ms
        )
        if result is not None and result.successful:
            ready = True
            break
        if result is not None and result.state == "error":
            self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
            raise TtsAttemptError(sentence_id, result.reason or "device_error")
        if send_index < len(retry_delays_ms):
            self.logger.bind(tag="xiaoxin.tts").warning(
                "tts_state=preparing sentence_id={} ready_retry={} failure_reason=ready_timeout".format(
                    sentence_id, send_index + 1
                )
            )
            await asyncio.sleep(retry_delays_ms[send_index] / 1000)
    if not ready:
        self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
        raise TtsAttemptError(sentence_id, "ready_timeout")
else:
    await send_tts_message(self, "start", sentence_id=sentence_id)
    self.client_is_speaking = True
    delay_ms = int(self.config.get("wakeup_response_start_delay_ms", 300))
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
```

Only after that block, enqueue the existing FIRST/MIDDLE/LAST DTOs and store the complete `text`. End the method without returning a sentence id.

- [ ] **Step 6: Add connection-to-dispatcher outcome callbacks**

```python
def _control_delivery_for_sentence(self, sentence_id: str) -> str | None:
    return self.xiaoxin_control_tts_deliveries.get(sentence_id)

def mark_xiaoxin_control_tts_done(self, sentence_id: str) -> None:
    delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
    if delivery_id and self.xiaoxin_control_runtime:
        self.xiaoxin_control_runtime.dispatcher.mark_tts_done(
            delivery_id, sentence_id
        )

def mark_xiaoxin_control_tts_failed(
    self, sentence_id: str, reason: str
) -> None:
    delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
    if delivery_id and self.xiaoxin_control_runtime:
        self.xiaoxin_control_runtime.dispatcher.mark_tts_attempt_failed(
            delivery_id, sentence_id, reason
        )

def mark_xiaoxin_control_tts_legacy_unverified(
    self, sentence_id: str
) -> None:
    delivery_id = self.xiaoxin_control_tts_deliveries.pop(sentence_id, None)
    if delivery_id and self.xiaoxin_control_runtime:
        self.xiaoxin_control_runtime.dispatcher.mark_tts_legacy_unverified(
            delivery_id, sentence_id
        )
```

Before unregistering a closing connection, iterate a copy and notify failures:

```python
for sentence_id in list(self.xiaoxin_control_tts_deliveries):
    self.mark_xiaoxin_control_tts_failed(
        sentence_id, "connection_closed_before_done"
    )
```

- [ ] **Step 7: Make `send_tts_message(stop)` branch on the typed done result**

For a control delivery, register the waiter before sending stop, wait up to the configured 10000 ms, then:

```python
reliable_mode = (
    is_control_delivery
    and hasattr(conn, "supports_reliable_notification_tts")
    and conn.supports_reliable_notification_tts()
)
result = None
if reliable_mode:
    result = await conn.wait_for_tts_ack(
        "done", stop_sentence_id, timeout_ms
    )

if is_control_delivery:
    if reliable_mode:
        if result is not None and result.successful:
            conn.mark_xiaoxin_control_tts_done(stop_sentence_id)
        else:
            reason = (
                result.reason
                if result is not None and result.reason
                else "done_timeout"
            )
            conn.mark_xiaoxin_control_tts_failed(
                stop_sentence_id, reason
            )
    else:
        conn.mark_xiaoxin_control_tts_legacy_unverified(
            stop_sentence_id
        )
```

Delete every unconditional `mark_xiaoxin_control_tts_done()` call after a timed-out waiter.

- [ ] **Step 8: Run server connection tests**

```powershell
python -m pytest tests/xiaoxin/test_connection_ack.py tests/xiaoxin/test_connection_integration.py tests/xiaoxin/test_tts_playback_ack.py -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit the attempt handshake slice**

```powershell
git add main/xiaozhi-server/core/connection.py main/xiaozhi-server/core/handle/sendAudioHandle.py main/xiaozhi-server/tests/xiaoxin/test_connection_ack.py main/xiaozhi-server/tests/xiaoxin/test_connection_integration.py
git commit -m "feat: require ready and done for notification tts"
```

---

### Task 8: Server delivery/attempt separation and unbounded online retry orchestration

**Files:**
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\xiaoxin\control_types.py:33-130`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\xiaoxin\delivery_store.py:28-100`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\xiaoxin\dispatcher.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\core\xiaoxin\control_runtime.py:30-47,146-153`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_dispatcher.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_registry_and_store.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_control_types.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_control_runtime.py`

**Interfaces:**
- Produces delivery state `retry_wait` and fields `event_acknowledged`, `tts_attempt_count`, `tts_state`, `tts_last_failure_reason`, `tts_playback_mode`.
- Produces store methods `begin_tts_attempt`, `mark_event_acknowledged`, `mark_tts_attempt_failed`, `mark_tts_done`, `mark_tts_legacy_unverified`.
- Produces dispatcher methods called by `ConnectionHandler`: `mark_tts_done`, `mark_tts_attempt_failed`, `mark_tts_legacy_unverified`.
- Event card and voice each own a background task; both must finish before delivery becomes done.

- [ ] **Step 1: Write failing store model tests**

Add the payload test to `test_control_types.py` and the store test to `test_registry_and_store.py`:

```python
def test_control_event_payload_uses_ack_capable_xiaoxin_event_shape():
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "course_reminder",
            "title": "上课提醒",
            "body": "十分钟后上课",
            "tag": "课程",
            "priority": 3,
            "ttl_ms": 8000,
        }
    )
    payload = build_xiaoxin_event_payload("delivery-1", request)
    assert payload == {
        "type": "xiaoxin_event",
        "delivery_id": "delivery-1",
        "event": "course_reminder",
        "title": "上课提醒",
        "body": "十分钟后上课",
        "tag": "课程",
        "priority": 3,
        "ttl_ms": 8000,
    }


def test_delivery_store_tracks_event_and_tts_attempts_separately():
    store = XiaoxinDeliveryStore()
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "提醒",
            "body": "内容",
            "speak": True,
            "speak_text": "完整提醒",
        }
    )
    record = store.create(request, build_xiaoxin_event_payload("ignored", request))
    store.mark_event_acknowledged(record.delivery_id, {"state": "device_received"})
    attempt = store.begin_tts_attempt(record.delivery_id, "sentence-1")
    store.mark_tts_attempt_failed(
        record.delivery_id, "sentence-1", "ready_timeout"
    )

    current = store.require(record.delivery_id)
    assert current.event_acknowledged is True
    assert attempt == 1
    assert current.tts_attempt_count == 1
    assert current.tts_state == "retry_wait"
    assert current.tts_last_failure_reason == "ready_timeout"
    assert current.state != XiaoxinDeliveryState.FAILED
```

- [ ] **Step 2: Write failing dispatcher retry tests**

Add these complete helpers and tests:

```python
from core.xiaoxin.tts_delivery import TtsAttemptError
from core.xiaoxin.control_types import build_xiaoxin_event_payload


class FlakyTtsConnection:
    def __init__(self, failures_before_done):
        self.failures_before_done = failures_before_done
        self.spoken = []
        self.dispatcher = None

    async def speak_from_control_console(
        self, text, delivery_id, sentence_id
    ):
        self.spoken.append((text, delivery_id, sentence_id))
        if len(self.spoken) <= self.failures_before_done:
            raise TtsAttemptError(sentence_id, "ready_timeout")
        self.dispatcher.mark_tts_done(delivery_id, sentence_id)


def _speaking_record(store):
    request = parse_control_event_request(
        {
            "device_id": "aa",
            "event": "notification",
            "title": "提醒",
            "body": "内容",
            "speak": True,
            "speak_text": "完整提醒文本",
        }
    )
    record = store.create(request, build_xiaoxin_event_payload("ignored", request))
    store.mark_event_acknowledged(record.delivery_id, {"state": "device_received"})
    return record


def test_tts_delivery_retries_with_new_sentence_ids_and_full_text():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FlakyTtsConnection(failures_before_done=2)
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0, 0, 0, 0),
        )
        conn.dispatcher = dispatcher
        record = _speaking_record(store)
        await dispatcher._run_tts_delivery(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert len(conn.spoken) == 3
    assert len({item[2] for item in conn.spoken}) == 3
    assert all(item[0] == "完整提醒文本" for item in conn.spoken)
    assert record.tts_attempt_count == 3
    assert record.state == XiaoxinDeliveryState.DONE


def test_retry_delay_list_does_not_cap_attempt_count():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        conn = FlakyTtsConnection(failures_before_done=5)
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0, 0, 0, 0),
        )
        conn.dispatcher = dispatcher
        record = _speaking_record(store)
        await dispatcher._run_tts_delivery(record.delivery_id)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert len(conn.spoken) == 6
    assert record.tts_attempt_count == 6
    assert record.state == XiaoxinDeliveryState.DONE


def test_stale_done_cannot_resolve_current_attempt():
    async def scenario():
        dispatcher = XiaoxinEventDispatcher(
            XiaoxinDeviceRegistry(),
            XiaoxinDeliveryStore(),
            FakeDoorbell(),
            retry_delays_seconds=(0,),
        )
        record = _speaking_record(dispatcher.store)
        dispatcher.store.begin_tts_attempt(record.delivery_id, "current")
        future = asyncio.get_running_loop().create_future()
        dispatcher._tts_outcomes[(record.delivery_id, "current")] = future
        dispatcher.mark_tts_done(record.delivery_id, "stale")
        stale_resolved = future.done()
        dispatcher.mark_tts_done(record.delivery_id, "current")
        current_outcome = await future
        return stale_resolved, current_outcome

    stale_resolved, outcome = asyncio.run(scenario())
    assert stale_resolved is False
    assert outcome.sentence_id == "current"
    assert outcome.status == "done"
```

- [ ] **Step 3: Run store and dispatcher tests to verify failure**

```powershell
python -m pytest tests/xiaoxin/test_control_types.py tests/xiaoxin/test_registry_and_store.py tests/xiaoxin/test_dispatcher.py -q
```

Expected: failures for missing fields, methods, retry state and new connection signature.

- [ ] **Step 4: Extend the delivery record without overloading terminal failure**

First replace `build_xiaoxin_event_payload()` with the ACK-capable protocol shape:

```python
def build_xiaoxin_event_payload(
    delivery_id: str, request: XiaoxinControlEventRequest
) -> dict[str, Any]:
    return {
        "type": "xiaoxin_event",
        "delivery_id": delivery_id,
        "event": request.event.value,
        "title": request.title,
        "body": request.body,
        "tag": request.tag,
        "priority": request.priority,
        "ttl_ms": request.ttl_ms,
    }
```

Add `RETRY_WAIT = "retry_wait"` to `XiaoxinDeliveryState` and these dataclass fields:

```python
event_acknowledged: bool = False
tts_attempt_count: int = 0
tts_state: str = "not_requested"
tts_last_failure_reason: str | None = None
tts_playback_mode: str | None = None
```

Expose all five in `to_dict()`. Keep `control_tts_sentence_id` as the current attempt id.

- [ ] **Step 5: Add atomic store operations**

```python
def mark_event_acknowledged(
    self, delivery_id: str, ack: dict[str, Any]
) -> XiaoxinDeliveryRecord:
    record = self.require(delivery_id)
    record.event_acknowledged = True
    return self.transition(
        delivery_id,
        XiaoxinDeliveryState.DEVICE_RECEIVED,
        source="device",
        details={"ack": dict(ack)},
    )

def begin_tts_attempt(
    self, delivery_id: str, sentence_id: str
) -> int:
    record = self.require(delivery_id)
    record.tts_attempt_count += 1
    record.control_tts_sentence_id = sentence_id
    record.tts_state = "preparing"
    record.tts_last_failure_reason = None
    self.transition(
        delivery_id,
        XiaoxinDeliveryState.SPEAKING,
        details={
            "attempt": record.tts_attempt_count,
            "sentence_id": sentence_id,
            "tts_state": "preparing",
        },
    )
    return record.tts_attempt_count

def mark_tts_attempt_failed(
    self, delivery_id: str, sentence_id: str, reason: str
) -> bool:
    record = self.require(delivery_id)
    if record.control_tts_sentence_id != sentence_id:
        return False
    record.tts_state = "retry_wait"
    record.tts_last_failure_reason = reason
    self.transition(
        delivery_id,
        XiaoxinDeliveryState.RETRY_WAIT,
        details={
            "attempt": record.tts_attempt_count,
            "sentence_id": sentence_id,
            "failure_reason": reason,
        },
    )
    return True

def mark_tts_done(self, delivery_id: str, sentence_id: str) -> bool:
    record = self.require(delivery_id)
    if record.control_tts_sentence_id != sentence_id:
        return False
    record.tts_state = "done"
    record.tts_playback_mode = "reliable"
    record.tts_last_failure_reason = None
    return True

def mark_tts_legacy_unverified(
    self, delivery_id: str, sentence_id: str
) -> bool:
    record = self.require(delivery_id)
    if record.control_tts_sentence_id != sentence_id:
        return False
    record.tts_state = "legacy_unverified"
    record.tts_playback_mode = "legacy_unverified"
    return True
```

Every method must call `_save_history()` and `_notify()` either through `transition()` or explicitly after a field-only change.

- [ ] **Step 6: Replace single-shot delivery with separate event and TTS loops**

In `XiaoxinEventDispatcher.__init__` use:

```python
def __init__(
    self,
    registry: XiaoxinDeviceRegistry,
    store: XiaoxinDeliveryStore,
    doorbell_client: Any,
    wake_timeout_seconds: float = 15,
    ack_timeout_seconds: float = 10,
    retry_delays_seconds: tuple[float, ...] = (2, 5, 15, 30),
):
    self.registry = registry
    self.store = store
    self.doorbell_client = doorbell_client
    self.wake_timeout_seconds = wake_timeout_seconds
    self.ack_timeout_seconds = ack_timeout_seconds
    self.retry_delays_seconds = tuple(retry_delays_seconds or (2, 5, 15, 30))
    self._event_tasks: dict[str, asyncio.Task[Any]] = {}
    self._tts_tasks: dict[str, asyncio.Task[Any]] = {}
    self._event_ack_futures: dict[str, asyncio.Future[None]] = {}
    self._tts_outcomes: dict[tuple[str, str], asyncio.Future[TtsAttemptOutcome]] = {}
    self._stopping = False
```

Preserve the existing single-shot `_deliver()` behavior for `speak=False`. For `speak=True`, `submit()` starts only `_run_event_delivery(delivery_id)`. The event loop starts the unique TTS task immediately after the first successful card send, so an offline speaking reminder issues one wake flow and the card is always sent before voice preparation:

```python
delivery_coro = (
    self._run_event_delivery(record.delivery_id)
    if request.speak
    else self._deliver(record.delivery_id)
)
self._track_task(
    self._event_tasks,
    record.delivery_id,
    delivery_coro,
)
```

```python
def _ensure_tts_task(self, record: XiaoxinDeliveryRecord) -> None:
    if not record.request.speak or record.delivery_id in self._tts_tasks:
        return
    self._track_task(
        self._tts_tasks,
        record.delivery_id,
        self._run_tts_delivery(record.delivery_id),
    )
```

Do not create duplicate notification payloads; both loops reference the one record and fixed `delivery_id`.

Update the existing test helper to wait for whichever tasks currently own the delivery:

```python
async def wait_for_delivery_task(self, delivery_id: str) -> None:
    while True:
        tasks = [
            task
            for task in (
                self._event_tasks.get(delivery_id),
                self._tts_tasks.get(delivery_id),
            )
            if task is not None
        ]
        if not tasks:
            return
        await asyncio.gather(*tasks)
        if (
            delivery_id not in self._event_tasks
            and delivery_id not in self._tts_tasks
        ):
            return
```

- [ ] **Step 7: Implement connected-time retry delay and connection acquisition**

```python
async def _wait_for_connection(self, device_id: str) -> Any:
    while not self._stopping:
        conn = self.registry.get_connection(device_id)
        if conn is not None:
            return conn
        if self._can_attempt_wake():
            self.doorbell_client.publish_wake(device_id)
        conn = await self.registry.wait_for_connected(device_id, 30.0)
        if conn is not None:
            return conn
    raise asyncio.CancelledError

async def _wait_connected_delay(
    self, device_id: str, delay_seconds: float
) -> None:
    remaining = max(delay_seconds, 0.0)
    loop = asyncio.get_running_loop()
    while remaining > 0:
        await self._wait_for_connection(device_id)
        slice_seconds = min(remaining, 0.25)
        started = loop.time()
        await asyncio.sleep(slice_seconds)
        if self.registry.get_connection(device_id) is not None:
            remaining -= max(loop.time() - started, 0.0)

def _retry_delay(self, failure_count: int) -> float:
    index = min(max(failure_count - 1, 0), len(self.retry_delays_seconds) - 1)
    return self.retry_delays_seconds[index]
```

- [ ] **Step 8: Implement the event-card loop with one stable delivery id**

```python
async def _run_event_delivery(self, delivery_id: str) -> None:
    failures = 0
    while not self._stopping:
        record = self.store.require(delivery_id)
        if record.event_acknowledged:
            self._maybe_complete(record)
            return
        if self.registry.get_connection(record.device_id) is None:
            self.store.transition(
                delivery_id,
                XiaoxinDeliveryState.RETRY_WAIT,
                details={"event_failure": "device_offline"},
            )
        conn = await self._wait_for_connection(record.device_id)
        future = asyncio.get_running_loop().create_future()
        self._event_ack_futures[delivery_id] = future
        try:
            await conn.send_xiaoxin_event(record.payload)
            self._ensure_tts_task(record)
            self.store.transition(
                delivery_id,
                XiaoxinDeliveryState.SENT,
                details={"event_attempt": failures + 1},
            )
            await asyncio.wait_for(future, self.ack_timeout_seconds)
        except (asyncio.TimeoutError, ConnectionError, RuntimeError):
            failures += 1
            self.store.transition(
                delivery_id,
                XiaoxinDeliveryState.RETRY_WAIT,
                details={"event_failure": "ack_or_connection_timeout"},
            )
            await self._wait_connected_delay(
                record.device_id, self._retry_delay(failures)
            )
        finally:
            self._event_ack_futures.pop(delivery_id, None)
```

In `handle_ack`, for `device_received`, call `store.mark_event_acknowledged()`, resolve `_event_ack_futures[delivery_id]`, and call `_maybe_complete(record)`. Do not start TTS from the ACK handler; the TTS loop is already unique.

- [ ] **Step 9: Implement the voice attempt loop with a fresh ID every time**

Add imports:

```python
import uuid
from core.xiaoxin.tts_delivery import (
    TtsAttemptError,
    TtsAttemptOutcome,
)
```

```python
async def _run_tts_delivery(self, delivery_id: str) -> None:
    failures = 0
    while not self._stopping:
        record = self.store.require(delivery_id)
        if record.tts_state in {"done", "legacy_unverified"}:
            self._maybe_complete(record)
            return
        if self.registry.get_connection(record.device_id) is None:
            self.store.transition(
                delivery_id,
                XiaoxinDeliveryState.RETRY_WAIT,
                details={"tts_failure": "device_offline"},
            )
        conn = await self._wait_for_connection(record.device_id)
        sentence_id = uuid.uuid4().hex
        self.store.begin_tts_attempt(delivery_id, sentence_id)
        key = (delivery_id, sentence_id)
        future = asyncio.get_running_loop().create_future()
        self._tts_outcomes[key] = future
        try:
            await conn.speak_from_control_console(
                record.request.speak_text,
                delivery_id,
                sentence_id,
            )
            outcome = await future
        except TtsAttemptError as exc:
            outcome = TtsAttemptOutcome(
                sentence_id=sentence_id,
                status="failed",
                reason=exc.reason,
            )
        except (ConnectionError, RuntimeError):
            outcome = TtsAttemptOutcome(
                sentence_id=sentence_id,
                status="failed",
                reason="connection_closed_before_done",
            )
        finally:
            self._tts_outcomes.pop(key, None)

        if outcome.status == "done":
            self.store.mark_tts_done(delivery_id, sentence_id)
            self._maybe_complete(self.store.require(delivery_id))
            return
        if outcome.status == "legacy_unverified":
            self.store.mark_tts_legacy_unverified(delivery_id, sentence_id)
            self._maybe_complete(self.store.require(delivery_id))
            return

        failures += 1
        self.store.mark_tts_attempt_failed(
            delivery_id,
            sentence_id,
            outcome.reason or "unknown_tts_failure",
        )
        self.logger.bind(tag=TAG).warning(
            "delivery_id={} attempt={} sentence_id={} tts_state=retry_wait delivery_retry={} failure_reason={}".format(
                delivery_id,
                self.store.require(delivery_id).tts_attempt_count,
                sentence_id,
                failures,
                outcome.reason or "unknown_tts_failure",
            )
        )
        await self._wait_connected_delay(
            record.device_id, self._retry_delay(failures)
        )
```

There is deliberately no `break` based on `failures`.

- [ ] **Step 10: Resolve only the current attempt and require both completion conditions**

```python
def _resolve_tts_outcome(
    self, delivery_id: str, outcome: TtsAttemptOutcome
) -> None:
    record = self.store.get(delivery_id)
    if record is None or record.control_tts_sentence_id != outcome.sentence_id:
        return
    future = self._tts_outcomes.get((delivery_id, outcome.sentence_id))
    if future is not None and not future.done():
        future.set_result(outcome)

def mark_tts_done(self, delivery_id: str, sentence_id: str) -> None:
    self._resolve_tts_outcome(
        delivery_id, TtsAttemptOutcome(sentence_id, "done")
    )

def mark_tts_attempt_failed(
    self, delivery_id: str, sentence_id: str, reason: str
) -> None:
    self._resolve_tts_outcome(
        delivery_id, TtsAttemptOutcome(sentence_id, "failed", reason)
    )

def mark_tts_legacy_unverified(
    self, delivery_id: str, sentence_id: str
) -> None:
    self._resolve_tts_outcome(
        delivery_id,
        TtsAttemptOutcome(sentence_id, "legacy_unverified"),
    )

def _maybe_complete(self, record: XiaoxinDeliveryRecord) -> None:
    if record.state == XiaoxinDeliveryState.DONE:
        return
    tts_complete = (
        not record.request.speak
        or record.tts_state in {"done", "legacy_unverified"}
    )
    if record.event_acknowledged and tts_complete:
        self.store.transition(record.delivery_id, XiaoxinDeliveryState.DONE)
```

- [ ] **Step 11: Add shutdown cancellation and runtime wiring**

```python
async def stop(self) -> None:
    self._stopping = True
    tasks = [*self._event_tasks.values(), *self._tts_tasks.values()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    self._event_tasks.clear()
    self._tts_tasks.clear()
```

Call `await self.dispatcher.stop()` from `XiaoxinControlRuntime.stop()` before stopping the doorbell client. Convert the global millisecond configuration when constructing the dispatcher:

```python
retry_delays_seconds = tuple(
    float(value) / 1000
    for value in config.get(
        "tts_delivery_retry_delays_ms",
        [2000, 5000, 15000, 30000],
    )
)
dispatcher = XiaoxinEventDispatcher(
    registry,
    store,
    doorbell_client,
    wake_timeout_seconds=float(control.get("wake_timeout_seconds", 15)),
    ack_timeout_seconds=float(control.get("ack_timeout_seconds", 10)),
    retry_delays_seconds=retry_delays_seconds,
)
```

- [ ] **Step 12: Update dispatcher tests and run them**

Keep the existing `test_offline_delivery_fails_immediately` for `speak=False`. Add this separate speaking-reminder test:

```python
def test_offline_speaking_delivery_waits_for_reconnect_instead_of_failing():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0,),
        )
        record = await dispatcher.submit(_request(speak=True))
        await asyncio.sleep(0.01)
        current = store.require(record.delivery_id)
        task_is_live = not dispatcher._event_tasks[record.delivery_id].done()
        await dispatcher.stop()
        return current, task_is_live

    record, task_is_live = asyncio.run(scenario())
    assert record.state == XiaoxinDeliveryState.RETRY_WAIT
    assert record.reason is None
    assert task_is_live is True
```

Replace `test_speaking_delivery_starts_tts_immediately_then_tts_done_finishes` with:

```python
def test_speaking_delivery_requires_card_ack_and_matching_tts_done():
    async def scenario():
        registry = XiaoxinDeviceRegistry()
        store = XiaoxinDeliveryStore()
        class PendingTtsConnection:
            def __init__(self):
                self.sent = []
                self.spoken = []

            async def send_xiaoxin_event(self, payload):
                self.sent.append(payload)

            async def speak_from_control_console(
                self, text, delivery_id, sentence_id
            ):
                self.spoken.append((text, delivery_id, sentence_id))

        conn = PendingTtsConnection()
        registry.register_connection("aa", conn, "websocket")
        dispatcher = XiaoxinEventDispatcher(
            registry,
            store,
            FakeDoorbell(),
            retry_delays_seconds=(0,),
        )
        record = await dispatcher.submit(_request(speak=True))
        while record.delivery_id not in dispatcher._tts_tasks:
            await asyncio.sleep(0)
        event_task = dispatcher._event_tasks[record.delivery_id]
        tts_task = dispatcher._tts_tasks[record.delivery_id]
        while not conn.spoken:
            await asyncio.sleep(0)
        await dispatcher.handle_ack(
            "aa",
            {
                "type": "xiaoxin_ack",
                "delivery_id": record.delivery_id,
                "state": "device_received",
            },
            conn,
        )
        dispatcher.mark_tts_done(record.delivery_id, conn.spoken[0][2])
        await asyncio.gather(event_task, tts_task)
        return store.require(record.delivery_id), conn

    record, conn = asyncio.run(scenario())
    assert conn.sent[0]["delivery_id"] == record.delivery_id
    assert len(conn.spoken) == 1
    assert record.event_acknowledged is True
    assert record.tts_state == "done"
    assert record.state == XiaoxinDeliveryState.DONE
```

```powershell
python -m pytest tests/xiaoxin/test_control_types.py tests/xiaoxin/test_dispatcher.py tests/xiaoxin/test_registry_and_store.py tests/xiaoxin/test_control_runtime.py -q
```

Expected: all selected tests pass, including sixth-attempt success and stale-done rejection.

- [ ] **Step 13: Commit reliable delivery orchestration**

```powershell
git add main/xiaozhi-server/core/xiaoxin/control_types.py main/xiaozhi-server/core/xiaoxin/delivery_store.py main/xiaozhi-server/core/xiaoxin/dispatcher.py main/xiaozhi-server/core/xiaoxin/control_runtime.py main/xiaozhi-server/tests/xiaoxin/test_control_types.py main/xiaozhi-server/tests/xiaoxin/test_dispatcher.py main/xiaozhi-server/tests/xiaoxin/test_registry_and_store.py main/xiaozhi-server/tests/xiaoxin/test_control_runtime.py
git commit -m "feat: retry notification tts until device completion"
```

---

### Task 9: Configuration, contract documentation and end-to-end verification

**Files:**
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\config.yaml`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\data\.config.yaml`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\config_from_api.yaml`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server\tests\xiaoxin\test_config_contract.py`
- Modify: `D:\AI_Pet\xiaoxin-esp32-server\docs\development\xiaoxin-tts-playback-ack.md`
- Test: both repositories' focused and full suites.

**Interfaces:**
- Finalizes the public configuration and protocol contract.
- Does not introduce persistence outside the running process.
- Does not edit the user's untracked real-device acceptance ledger.

- [ ] **Step 1: Write failing configuration contract tests**

Append:

```python
def test_reliable_notification_tts_defaults_are_declared_everywhere():
    for config_name in ("config.yaml", "data/.config.yaml", "config_from_api.yaml"):
        cfg = load_config_file(config_name)
        assert cfg["tts_ready_ack_timeout_ms"] == 700
        assert cfg["tts_ready_start_retry_delays_ms"] == [300, 600, 1200]
        assert cfg["tts_delivery_retry_delays_ms"] == [2000, 5000, 15000, 30000]
        assert cfg["tts_done_ack_timeout_ms"] == 10000
```

- [ ] **Step 2: Run the config test and verify failure**

```powershell
python -m pytest tests/xiaoxin/test_config_contract.py -q
```

Expected: failure because retry arrays are absent and done timeout is still 5000 ms in at least one config.

- [ ] **Step 3: Set identical defaults in all supported configs**

```yaml
tts_ready_ack_timeout_ms: 700
tts_ready_start_retry_delays_ms: [300, 600, 1200]
tts_delivery_retry_delays_ms: [2000, 5000, 15000, 30000]
tts_done_ack_timeout_ms: 10000
```

Remove `xiaoxin_control.tts_done_timeout_seconds`; done timing begins only after stop and is controlled by `tts_done_ack_timeout_ms`.

- [ ] **Step 4: Update the ACK contract to match implemented semantics**

The document must state all of the following explicitly:

```markdown
- Strong reliable notification playback requires ready, done, and pre-roll capability flags together.
- Reminder cards use type=xiaoxin_event, carry the stable delivery_id, and ACK device_received with that same id.
- ACK matching uses the current connection/session plus sentence_id and state.
- Device error ACKs use state=error and an enumerated reason.
- Ready timeout does not enqueue text or complete the delivery; it fails only the current attempt.
- Done is sent only after pre-roll, decode, playback, active output, and I2S playback time are drained.
- A done timeout or connection close creates a new sentence_id and restarts from the full original text.
- The 30-second retry value is a delay cap, not a retry-count cap.
- Legacy devices are marked legacy_unverified and are outside the strong guarantee.
- Reliable retries survive ordinary connection replacement within one service process, but not process restart.
```

Delete the old statements that ready timeout falls through to normal sending or that done timeout completes cleanup as success.

- [ ] **Step 5: Run the complete server TTS and dispatcher test set**

From `D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server`:

```powershell
python -m pytest tests/xiaoxin/test_tts_playback_ack.py tests/xiaoxin/test_connection_ack.py tests/xiaoxin/test_connection_integration.py tests/xiaoxin/test_control_types.py tests/xiaoxin/test_dispatcher.py tests/xiaoxin/test_registry_and_store.py tests/xiaoxin/test_control_runtime.py tests/xiaoxin/test_config_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Run the full server Xiaoxin suite**

```powershell
python -m pytest tests/xiaoxin -q
```

Expected: zero failures. Do not hide unrelated existing failures; record them and stop before claiming completion.

- [ ] **Step 7: Run the complete firmware regression suite**

From `D:\AI_Pet\hzcu_xiaoxin_firmwire_private`:

```powershell
python -m pytest tests -q
g++ -std=c++17 -I tests/stubs -I main/audio tests/tts_playback_session_test.cc main/audio/tts_playback_session.cc -o build/host-tests/tts_playback_session_test.exe
& build/host-tests/tts_playback_session_test.exe
```

Expected: zero pytest failures and host test exit `0`.

- [ ] **Step 8: Rebuild the target firmware in the ESP-IDF environment**

```powershell
idf.py build
```

Expected: target remains `WAVESHARE_ESP32_S3_TOUCH_LCD_1_46`, build exits `0`, and no unrelated `sdkconfig` changes appear.

- [ ] **Step 9: Perform the real-device acceptance sequence**

Use one fixed 80-120 Chinese-character reminder and record these runs without editing the user's untracked ledger unless explicitly authorized:

1. Main screen playback three times.
2. Low-power clock playback three times.
3. Artificial ready ACK delay of 500 ms.
4. One forced duplicate start using the same sentence id.
5. WebSocket disconnect before done, then reconnect and verify a new sentence id starts at the first character.
6. Near-capacity pre-roll followed by stop; verify done arrives inside 10 seconds without truncating the tail.
7. Four failed delivery attempts followed by recovery; verify the fifth and later attempts continue instead of expiring.

Every run must show: screen clock timer stopped before ready, no missing first character, no missing final character, no audio splice across attempts, no duplicate notification card, and delivery done only after the matching device done.

- [ ] **Step 10: Inspect both repositories before final commits**

```powershell
git -C D:\AI_Pet\hzcu_xiaoxin_firmwire_private diff --check
git -C D:\AI_Pet\hzcu_xiaoxin_firmwire_private status --short
git -C D:\AI_Pet\xiaoxin-esp32-server diff --check
git -C D:\AI_Pet\xiaoxin-esp32-server status --short
```

Expected: only intended implementation files are modified. In the server repository, `docs/README.md` and `docs/operations/xiaoxin-real-device-acceptance-ledger.md` remain unstaged and untouched.

- [ ] **Step 11: Commit configuration and contract documentation**

```powershell
git add main/xiaozhi-server/config.yaml main/xiaozhi-server/data/.config.yaml main/xiaozhi-server/config_from_api.yaml main/xiaozhi-server/tests/xiaoxin/test_config_contract.py docs/development/xiaoxin-tts-playback-ack.md
git commit -m "docs: finalize reliable notification tts contract"
```

## Final Completion Checklist

- [ ] Firmware hello advertises all four reliable TTS capability fields on WebSocket and MQTT.
- [ ] `tts:start` synchronously owns incoming audio before any main-task scheduling.
- [ ] Waveshare 1.46 exits the low-power clock and stops its 50 ms refresh before ready.
- [ ] ResetDecoder runs before the session becomes PLAYING and never runs after ready for that attempt.
- [ ] Ordered ingress preserves all packets across decode queue backpressure and rejects packet 85 without dropping packet 1.
- [ ] Duplicate start is idempotent; a from-head replay always uses a new sentence id.
- [ ] done waits for ingress, decode, playback, active output and expected I2S playback end.
- [ ] Server does not submit text before ready and does not mark done after a timed-out done waiter.
- [ ] Card ACK and TTS completion are independent and both are required for delivery done.
- [ ] Ready/error/done from an old connection or old sentence cannot complete the current attempt.
- [ ] Retry delays cap at 30 seconds and attempts continue without a maximum count while the process lives.
- [ ] Legacy devices are visibly `legacy_unverified` rather than falsely verified.
- [ ] Targeted tests, full server tests, full firmware tests, firmware build and real-device acceptance all pass.
