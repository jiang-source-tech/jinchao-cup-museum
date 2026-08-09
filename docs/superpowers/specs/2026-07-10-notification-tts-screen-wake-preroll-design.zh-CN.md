# 通知 TTS 屏保唤醒、预缓冲与可靠播报设计

## 目标

通知、课程提醒和待办提醒触发 TTS 时，设备必须先退出低功耗时钟屏保、停止屏保动画刷新并恢复主界面，然后从第一帧开始完整播放语音。

系统同时满足两个不可退让的条件：

1. 提醒播报不能因为一次 ready 超时而被直接取消；
2. 屏幕切换、状态调度、超时回退或重试不能造成语音开头丢失。

本设计采用三层保护：

- `tts ready` 握手负责正常时序；
- ESP32 pre-roll 队列负责吸收任何提前到达的音频；
- 服务端保留提醒文本和播报尝试状态，ready 超时或连接中断时从头重试。

### 可靠性保证边界

在服务进程持续运行且设备最终重新上线的前提下，需要语音的 delivery 不因 ready 超时、done 超时、连接中断或有限次数的 attempt 失败而过期。服务端持续重试，直到收到当前 attempt 的匹配 done，或用户/运维显式取消该 delivery。

重试退避中的 30 秒是单次等待间隔上限，不是总重试次数上限。达到 30 秒后继续每 30 秒创建新 attempt；设备离线期间暂停，重新上线后恢复。

本轮不把待播报 delivery 持久化到服务进程之外。因此服务进程崩溃、重启或内存状态丢失后的恢复不属于本轮“最终播报”保证。这个边界必须在实现、测试和运行手册中明确记录，不能把进程内可靠重试描述成跨重启的持久投递。

## 当前问题

### 屏保不会因语音通道打开而退出

目标板创建 `PowerSaveTimer(-1, 60, -1)`，其中 `-1` 表示低功耗时钟屏保不会降低 CPU 频率。屏保显示后，每 50 ms 刷新一次全屏 LVGL 动画。

音频通道打开时，应用调用 `SetPowerSaveLevel(PERFORMANCE)`，但目标板没有像其他部分板型一样在性能模式下调用 `power_save_timer_->WakeUp()`。因此 Wi-Fi 已切到性能模式时，低功耗时钟图层和 50 ms 动画定时器仍可能继续运行。

### TTS start 与音频接收之间存在竞态

固件收到 `tts:start` 后，通过主任务异步切换到 `kDeviceStateSpeaking`。音频回调当前只在设备已经处于 Speaking 时入队，所以 start 已收到但状态尚未切换期间到达的首批音频会被丢弃。

### ResetDecoder 可能清掉提前到达的音频

进入 Speaking 时会调用 `ResetDecoder()`。如果提前音频直接进入现有 decode 队列，随后执行的 ResetDecoder 会清空这些音频。因此仅放宽 Speaking 状态判断不足以解决首包丢失，必须使用独立 pre-roll 队列。

### 服务端当前忽略 ready 等待结果

通知播报路径虽然会等待 `tts ready`，但等待结果没有决定后续行为。ready 超时后仍继续启动 TTS，无法证明设备已退出屏保并准备好播放。

### 服务端完成不等于设备播放完成

固件尚未实现 `tts done`。服务端只能估算发送和播放完成时间，不能确认设备最后一帧已经交给扬声器路径。

## 范围

本设计覆盖：

- 控制台通知播报；
- 课程提醒播报；
- 待办提醒播报；
- 其他通过 `speak_from_control_console()` 进入的主动提醒 TTS。

普通对话和唤醒词回复继续兼容现有路径，但固件新增的 ready、done 和 pre-roll 能力可以被它们复用。

## 协议能力

本规格扩展并收紧 `docs/development/xiaoxin-tts-playback-ack.md`。原 ACK 文档仍定义普通 TTS 的基础兼容协议；对于本规格覆盖的主动提醒，若两份文档在 ready 超时回退、done 完成条件或 ACK 匹配规则上冲突，以本规格为准。实施时必须同步更新基础 ACK 文档，避免长期保留两套互相矛盾的语义。

新固件在 hello 中声明：

```json
{
  "features": {
    "mcp": true,
    "tts_ready_ack": true,
    "tts_done_ack": true,
    "tts_preroll_buffer": true,
    "tts_preroll_capacity_ms": 5040
  }
}
```

字段语义：

- `tts_ready_ack`：设备只在屏保退出、旧音频清理和播放管线就绪后发送 ready；
- `tts_done_ack`：设备只在 pre-roll、decode、playback 和 I2S DMA 全部排空、最后一个采样到达扬声器时间线后发送 done；
- `tts_preroll_buffer`：设备能在 Preparing 阶段保存提前到达的音频而不丢弃；
- `tts_preroll_capacity_ms`：设备声明 pre-roll 最大音频覆盖时长，首版为 5040 ms，即 84 个 60 ms Opus 包。

主动提醒进入强可靠模式的必要条件是 `tts_ready_ack && tts_done_ack && tts_preroll_buffer`。三个能力可以继续被普通 TTS 独立使用，但缺少任意一个时，服务端都不能宣称该设备满足“绝不丢首包且设备实际播完后才完成”的保证。

ready 和 done 继续使用现有消息结构：

```json
{
  "type": "tts",
  "state": "ready",
  "session_id": "...",
  "sentence_id": "..."
}
```

失败 ACK 使用统一结构：

```json
{
  "type": "tts",
  "state": "error",
  "session_id": "...",
  "sentence_id": "...",
  "reason": "preroll_overflow"
}
```

`reason` 还可以是 `playback_drain_timeout` 或其他明确枚举值。服务端按当前连接代际、`session_id`、`sentence_id` 和 state 共同匹配 ACK；缺少 sentence_id、来自旧连接或不属于当前 attempt 的 ACK 只能记为 stale，不能推进状态。

```json
{
  "type": "tts",
  "state": "done",
  "session_id": "...",
  "sentence_id": "..."
}
```

## 端到端正常时序

```text
提醒事件创建
  -> 事件正文和提醒卡片发送到设备
  -> 服务端创建 sentence_id 并注册 ready waiter
  -> 服务端发送 tts:start(sentence_id)
  -> 设备同步进入 TTS_PREPARING
  -> 设备退出 PowerSaveTimer 屏保
  -> 停止 50 ms 屏保刷新定时器
  -> 隐藏低功耗时钟图层并恢复主界面
  -> 切换 Speaking，ResetDecoder，准备 Opus/I2S/扬声器
  -> 设备启动 pre-roll 有序排空泵并发送 tts:ready(sentence_id)
  -> 服务端开始生成并发送该句完整音频
  -> 服务端发送 tts:stop(sentence_id)
  -> 设备等待 decode/playback/I2S 路径排空
  -> 设备发送 tts:done(sentence_id)
  -> 服务端将提醒播报标记为 done
```

同一 WebSocket 上文本消息和二进制消息保持顺序。正常情况下，服务端在收到 ready 前不会产生该提醒的首个音频包，因此第一帧不会与屏幕切换竞争。

## 固件设计

### 板级播放准备接口

在 `Board` 增加默认空实现的播放准备接口：

```cpp
virtual void PrepareForAudioPlayback() {}
```

Waveshare 1.46 目标板实现该接口：

1. 调用 `power_save_timer_->WakeUp()`；
2. 由现有 `OnExitSleepMode` 回调隐藏低功耗时钟图层；
3. 停止 `low_power_clock_timer_`；
4. 恢复主界面和正常背光；
5. 将 Wi-Fi 电源策略切换为 PERFORMANCE。

接口必须幂等。设备本来不在屏保时调用不得改变当前主界面、通知页或设置页状态。

目标板同时覆盖 `SetPowerSaveLevel()`：当 level 不是 LOW_POWER 时先唤醒 `PowerSaveTimer`，再调用 `WifiBoard::SetPowerSaveLevel(level)`。这样音频通道打开、OTA 和其他性能场景也不会遗留屏保刷新定时器。

### TTS 播放准备状态

Application 增加独立于 `DeviceState` 的内部状态：

```text
IDLE
PREPARING
PLAYING
DRAINING
```

状态语义：

- `IDLE`：没有活动 TTS；
- `PREPARING`：已经收到 start，正在退出屏保和准备音频管线；
- `PLAYING`：设备已准备好，正常接收和播放音频；
- `DRAINING`：已收到 stop，不再期待新音频，等待本地队列排空。

内部状态保存当前 `sentence_id` 和代际编号。所有延迟回调在执行前必须检查代际，防止旧句子的 ready、done 或清理动作影响新句子。

### tts:start 处理

收到有效 `tts:start` 时，网络回调先同步完成轻量状态更新：

1. 验证并复制 `sentence_id`；
2. 若是新 sentence，清空旧 pre-roll 和旧 ACK 状态；
3. 将内部状态设置为 PREPARING；
4. 再把重量级准备过程调度到主任务。

同步设置 PREPARING 必须发生在 Schedule 之前，从而关闭当前“start 已收到但音频仍按非 Speaking 丢弃”的窗口。

重复收到相同 sentence 的 start 必须幂等：

- 仍在 PREPARING：继续当前准备，不清除已保存的 pre-roll；
- 已在 PLAYING：重新发送 ready，不重置 decoder，不重播已有音频；
- 已在 DRAINING：不重启播放；若本地尚未完成则继续 drain，完成后重发缓存的最终 done 结果；
- 已在 IDLE 且该 sentence 已有最终结果：重发缓存的 done 或失败结果，不重新播放；
- 已在 IDLE 且 sentence 从未出现：把它作为新的播放 attempt，进入 PREPARING；
- 已在 IDLE 且 sentence 已被旧代际中止但没有最终结果：视为过期 start 并记录。真正的重新播报必须由服务端使用新的 sentence_id 创建新 attempt。

### 主任务准备过程

主任务按固定顺序执行：

1. `Board::PrepareForAudioPlayback()`；
2. 切换到 `kDeviceStateSpeaking`；
3. 停止输入侧语音处理和非必要唤醒任务；
4. `ResetDecoder()` 清理上一段语音；
5. 确认 Opus decoder、输出重采样器和 I2S 输出可用；
6. 启动当前代际的单一有序输入泵，按 decode 队列可用容量增量搬运 pre-roll；
7. 将内部状态改为 PLAYING；
8. 发送当前 sentence 的 ready ACK。

ready 必须是最后一步。任何可能清空、重建或暂停播放队列的动作都不能发生在 ready 之后。ready 表示播放管线已经稳定且有序输入泵已经接管 pre-roll，不要求 84 个包一次性全部塞入容量更小的 decode 队列。

### pre-roll 队列

Application 持有独立的：

```cpp
std::deque<std::unique_ptr<AudioStreamPacket>> tts_preroll_queue_;
```

音频接收规则：

- PREPARING：进入 pre-roll，不解码、不播放；
- PLAYING 且 pre-roll 已空：进入现有 decode 队列；
- PLAYING 且 pre-roll 尚未排空：追加到同一有序输入尾部，不能直接进入 decode 队列；
- DRAINING：视为协议异常，记录并忽略迟到包；
- IDLE：维持现有无效音频丢弃行为。

pre-roll 和后续实时包必须由同一个串行输入泵提交给 `PushPacketToDecodeQueue()`。输入泵每次只按 decode 队列剩余容量搬运，保留 WebSocket 接收顺序；任何后到包都不能越过先到的 pre-roll。这样即使 84 包 pre-roll 大于现有 40 包 decode 队列，也不会因一次性搬运而阻塞主任务、溢出 decode 队列或打乱语音顺序。

pre-roll 上限为 84 包。正常 ready 握手下该队列应保持为空或只有极少数包。达到上限表示服务端在设备长时间未 ready 时仍持续发送，是协议异常。

达到容量上限时不能丢弃队首或中间帧，因为这会造成残缺语音。设备执行以下动作：

1. 保留已经缓存的前 84 帧；
2. 发送 `tts` 错误 ACK，reason 为 `preroll_overflow`；
3. 中止当前尝试并清空播放路径；
4. 服务端从第 0 帧重新发起新的播报尝试。

这不是取消提醒，而是取消一次已经无法保证完整性的播放尝试。

### tts:stop 与 done

收到 stop 后不能立即切回 Idle 或 Listening，也不能调用会清空 decoder 的路径。

处理顺序：

1. 内部状态进入 DRAINING；
2. 在独立任务中等待 pre-roll、有序输入泵、decode 队列和 playback 队列全部为空；
3. 等待输出任务确认当前代际最后一个 PCM 块已经调用 `OutputData()`；
4. 等待 codec/I2S 输出 drain fence，确认最后一个 PCM 采样已经离开 DMA 并到达扬声器时间线；
5. 发送 done ACK，并缓存该 sentence 的最终结果用于重复 stop/start 的幂等响应；
6. 清理当前 sentence 和 pre-roll；
7. 再切换 Idle 或 Listening。

`AudioService::WaitForPlaybackQueueEmpty()` 只观察软件队列，不能直接作为 done 条件。实现必须增加按 TTS 代际隔离的 playback-drained 等待接口：先观察软件队列和输出任务完成序号，再调用 codec 的输出排空能力。若具体 I2S 驱动不能报告 DMA 空闲，则必须根据最后一个 PCM 块的采样数和输出采样率计算剩余播放时长，再加固定安全余量；不能用 `OutputData()` 返回时间冒充实际播完时间。

等待过程必须有本地看门狗。看门狗超时发送带 reason 的失败 ACK，由服务端重新投递该提醒，而不是静默标记完成。

### 中止与新句子

用户主动打断、新通知抢占或连接关闭时：

- 增加 TTS 代际；
- 清空旧 pre-roll；
- ResetDecoder；
- 不发送旧 sentence 的 ready 或 done；
- 通知服务端该播放尝试没有完整完成。

提醒记录本身继续存在，是否重新排队由服务端决定。

## 服务端设计

### 播报尝试与提醒记录分离

`delivery_id` 表示必须完成的提醒投递，生命周期跨越重试。

`sentence_id` 表示一次具体播放尝试。每次必须从头重播时创建新的 sentence_id，并把 attempt 编号写入投递时间线。

因此：

- ready 超时只失败当前 attempt，不失败 delivery；
- pre-roll overflow 只失败当前 attempt，不失败 delivery；
- 连接断开只中止当前 attempt，不把 delivery 标记 done；
- 只有匹配当前 attempt 的 done ACK 才能完成 delivery。

### ready 等待必须读取结果

`speak_from_control_console()` 必须使用 `wait_for_tts_ack()` 的布尔结果。

正常路径：

1. 注册 waiter；
2. 发送 start；
3. 等待 ready；
4. ready 成功后才向 TTS provider 提交文本。

首个 ready 超时时，不生成或发送音频，而是对同一 attempt 重新发送 start。短重试退避为：

```text
300 ms -> 600 ms -> 1200 ms
```

重复 start 使用同一 sentence_id，固件必须按前述幂等规则回复。

三次短重试仍未 ready 时，中止当前 attempt，并由 dispatcher 创建新的 attempt。提醒 delivery 保持待播报状态，不进入 done。

### pre-roll 能力作为异常保护

服务端正常路径不能依赖 pre-roll，仍必须等 ready 后发音频。

只有以下历史兼容场景可能在 ready 前产生音频：

- 旧服务端已开始发送；
- start 和音频由旧路径并发产生；
- ready ACK 在回程网络中延迟，但设备实际已经准备好。

新固件的 pre-roll 负责保护这些场景。新服务端不得把 `tts_preroll_buffer` 解释为“可以跳过 ready”。

### 重新投递

dispatcher 为需要语音的 delivery 维护待播报状态和 attempt 计数。

以下事件触发从第 0 帧开始的新 attempt：

- ready 连续超时；
- 设备报告 `preroll_overflow`；
- 播放 drain 超时；
- WebSocket 在 done 前断开；
- 当前连接被新的设备会话替换。

重试必须使用原始 `speak_text` 重新生成完整语音，不能从上次已发送位置续传。这样不需要把 Qwen 的流式生成会话跨连接保存，也能保证新 attempt 从完整句首开始。

在线重试使用有界退避，避免故障设备形成忙循环。推荐：

```text
2 s -> 5 s -> 15 s -> 30 s
```

上式限制的是重试频率，不限制重试总次数。第四次等待后仍未完成时，继续以 30 秒为上限重复创建新 attempt，delivery 始终保持 pending。

设备离线时暂停计时；同一设备重新注册后恢复待播报 delivery。提醒卡片投递和语音播报分别幂等，重试语音不得重复创建通知卡片。

首版保证同一服务进程生命周期内的可靠重试。跨服务重启的持久化待播报队列不在本次范围内，后续若要求进程重启后仍保证播报，需要把 delivery store 从内存迁移到持久化存储。实现不得设置隐式最大 attempt 数、静默过期时间或“重试耗尽即成功”的回退。

### 完成条件

主动提醒 delivery 只有满足以下全部条件才能进入 done：

1. 设备已 ACK 事件正文或通知卡片；
2. 当前 attempt 收到匹配 sentence_id 的 ready；
3. 服务端已完整发送该 attempt 的音频和 stop；
4. 设备收到 stop 后排空 pre-roll、decode、playback 和 I2S DMA 的完整本地播放路径；
5. 服务端收到匹配当前 attempt 的 done。

旧 sentence、无 sentence_id 或上一个连接的 ACK 只能记录为过期消息，不能完成当前 delivery。

## 屏幕行为

退出屏保时不播放转场动画。设备直接隐藏低功耗时钟覆盖层，露出已经存在的主界面。

通知卡片可以继续显示在主界面上方，但不得继续运行低功耗时钟的 50 ms 动画定时器。

TTS 完成后不立即重新进入屏保。`PowerSaveTimer::WakeUp()` 将空闲计时清零，设备重新满足 Idle、音频通道关闭和音频服务空闲条件满 60 秒后再进入屏保。

## 兼容策略

### 新服务端 + 旧固件

旧固件不声明 ready、done 或 pre-roll：

- 保留旧延时发送行为；
- 不承诺屏保场景下绝不丢首包；
- 结果只能标记为 `legacy_unverified`，不能复用强可靠模式的 done 含义；
- 控制台和日志明确标记为 legacy playback，并提示升级固件；
- 需要“必须完整播报”的提醒只能在三个可靠能力全部具备后进入强可靠交付。旧固件仍可做兼容播报，但不在本规格的最终播报保证内。

### 旧服务端 + 新固件

新固件的 PREPARING/pre-roll 能保护旧服务端在 start 后立即发送的首批音频。若旧服务端从未等待 ready，设备仍会在主界面和播放管线准备好后从 pre-roll 第一帧开始播放。

### 非目标板

`Board::PrepareForAudioPlayback()` 默认空实现，非 Waveshare 1.46 板型不会因本改动改变屏幕或电源行为，但仍可复用 ready、done 和 pre-roll 状态机。

## 配置

服务端新增或调整：

```yaml
tts_ready_ack_timeout_ms: 700
tts_ready_start_retry_delays_ms: [300, 600, 1200]
tts_delivery_retry_delays_ms: [2000, 5000, 15000, 30000]
tts_done_ack_timeout_ms: 10000
```

ready timeout 保持 700 ms，因为直接隐藏屏保图层不应接近该上限。若实机测量超过 700 ms，必须先查找阻塞点，不能仅靠扩大超时掩盖问题。

done timeout 从服务端发送 stop 后开始计时。首版设为 10 秒，是因为最坏情况下 pre-roll、40 包 decode 队列和 2 帧 playback 队列可能同时存在，5 秒不足以覆盖 5040 ms pre-roll 及后续软件队列、I2S DMA 和调度余量。实机应记录实际 `done_wait_ms`，后续只能基于测量收紧该值。

## 可观测性

使用稳定日志字段：

- `delivery_id`
- `attempt`
- `sentence_id`
- `tts_state`
- `screen_wake_ms`
- `start_to_ready_ms`
- `preroll_packets`
- `ready_retry`
- `delivery_retry`
- `done_wait_ms`
- `failure_reason`

关键异常原因：

- `ready_timeout`
- `preroll_overflow`
- `connection_closed_before_done`
- `playback_drain_timeout`
- `stale_ack`

正常逐帧音频不输出串口日志，避免日志本身破坏实时性。

## 测试策略

### 服务端测试

- ready 成功前不向 TTS provider 提交文本；
- ready 成功后只提交一次文本；
- ready 超时按 300/600/1200 ms 重发同一 sentence 的 start；
- 短重试耗尽后创建新 attempt，但 delivery 不进入 failed 或 done；
- pre-roll overflow、连接中断和 done 超时都从原始文本创建新 attempt；
- 旧 attempt 的 ready/done 不能完成新 attempt；
- 只有当前 attempt 的 done 才完成 delivery；
- 重试语音不重复发送通知卡片。

### 固件主机侧测试

- start 在 Schedule 前同步进入 PREPARING；
- PREPARING 音频进入 pre-roll，不进入 decode 队列；
- ResetDecoder 后由有序输入泵按 decode 队列可用容量增量搬运 pre-roll；
- ready 发生在屏保退出、ResetDecoder 和有序输入泵接管 pre-roll 之后；
- 重复 start 幂等；
- 新 sentence 清理旧代际；
- pre-roll 大于 decode 队列容量时按序增量搬运，后续实时包不能越过 pre-roll；
- pre-roll 达到 84 包时报告 overflow，不能丢弃队首后继续播放；
- stop 后先排空软件队列和 I2S DMA，再 done，再切 Idle/Listening。

### 固件路径测试

- hello 包含 ready、done、pre-roll 能力；
- 主动提醒只有三个能力全部声明时才进入强可靠模式；
- Waveshare 1.46 的播放准备路径会 WakeUp PowerSaveTimer；
- 退出屏保会停止 50 ms 定时器；
- 非目标板的默认播放准备接口不改变行为；
- 旧服务端立即发送音频时，首包仍保存在 pre-roll。

### 实机验收

固定使用同一句 80 至 120 个汉字的提醒文本：

1. 主界面播报三次；
2. 进入低功耗时钟屏保后播报三次；
3. ready ACK 人为延迟 500 ms 后播报一次；
4. 人为制造一次 start 重发；
5. 播放期间短暂断开 WebSocket，恢复后确认新 attempt 从句首重播；
6. 人为填充接近 84 包 pre-roll 后发送 stop，确认 10 秒 done 窗口不会误判正常 drain；
7. 每次确认主界面先出现、屏保动画停止、首字完整、末字完整且只在 done 后完成 delivery。

验收失败条件：

- 缺少第一个字或前半句；
- 两个 attempt 的音频拼接；
- 屏保动画在播放期间继续运行；
- 没有 done 就把 delivery 标记完成；
- ready 超时后提醒永久消失；
- 达到第四次 delivery 重试后停止重试或把 delivery 标记失败；
- 重试导致通知卡片重复出现。

## 非目标

- 本轮不通过降低音质、关闭 EQ/DRC 或减少音量解决问题；
- 本轮不把增大服务端初始预发送包数作为主要修复；
- 本轮不增加屏保退出动画；
- 本轮不保证服务进程重启后的待播报持久性；
- 本轮不改变提醒正文、卡片布局或业务触发规则。
