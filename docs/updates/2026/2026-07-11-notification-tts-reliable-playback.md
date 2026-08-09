# UPD-20260711-001：通知 TTS 可靠播放与屏保唤醒

## 1. 更新信息

| 字段 | 内容 |
|---|---|
| 更新编号 | `UPD-20260711-001` |
| 更新主题 | 通知 TTS 可靠播放与屏保唤醒 |
| 状态 | 已发布 |
| 更新类型 | 缺陷修复、可靠性增强、协议完善、配置更新、文档更新 |
| 开始时间 | 2026-07-10，具体开始时间未记录 |
| 服务端合并时间 | 2026-07-11 07:19:45（UTC+8） |
| 固件合并时间 | 2026-07-11 07:19:45（UTC+8） |
| 发布时间 | 两个仓库的 `main` 均于 2026-07-11 推送到 GitHub，具体推送时间未记录 |
| 服务端仓库 | `jiang-source-tech/xiaoxin-esp32-server` |
| 固件仓库 | `jiang-source-tech/hzcu_xiaoxin_firmwire_private` |
| 目标分支 | 两个仓库均为 `main` |
| 服务端合并提交 | `5a99cd0aeb97fed2233717276d4f2d906f37742c` |
| 固件合并提交 | `1b483480717bf333f4bef176e911d761834503d7` |

## 2. 更新摘要

本次更新把通知 TTS 从“服务端发送了音频就近似认为完成”改为“设备准备完成后才接收音频，设备确认真实播放完成后服务端才结束投递”。设备处于屏保或省电界面时，会先建立本次播放的界面与音频所有权，再通过 `ready` 通知服务端开始发送。播放过程中出现断线、解码失败、输出超时或 ACK 超时时，服务端不会静默丢弃提醒，而是使用新的句子标识完整重试。

该更新直接降低语音开头丢失、旧音频混入新会话、真实播完后错误重试、通知播完后错误进入监听状态等风险。它没有证明屏幕刷新造成 CPU 不足是声音断续的唯一根因；CPU、I2S 和调度压力仍需通过真实设备测量。

## 3. 更新背景

### 3.1 原始现象

- 设备开启省电模式并停留在屏保界面时，通知 TTS 可能断断续续。
- 屏保退出、主界面恢复和语音播放可能在同一时间发生。
- 部分情况下可能只听见后半段，前半段语音已经到达但播放链路尚未准备好。
- 服务端缺少设备端真实完成信号，无法严格区分“发送完毕”和“设备播完”。
- 连接关闭、旧解码任务和迟到 ACK 存在污染或错误完成后续提醒的风险。

### 3.2 已确认事实

- 旧流程没有以设备 `ready` 作为二进制音频发送前置条件。
- 旧流程没有用严格匹配的设备 `done` 作为可靠提醒成功条件。
- 通知唤醒与 TTS 音频实际使用两种不同职责的传输：Doorbell MQTT 负责通知和唤醒，WebSocket/TCP 负责 TTS 控制、二进制音频和 ACK。
- 固件旧输出路径存在 I2S 写入可能无限等待的代码路径，使播放错误无法可靠收敛。

### 3.3 推断与未知项

- 屏幕刷新或界面恢复可能增加 CPU、内存带宽或任务调度压力，但“CPU 不足导致全部断续现象”没有实机性能数据支持。
- 真实设备上的 I2S underrun、队列深度、屏幕刷新耗时和音频任务调度抖动尚未采集。
- 当前无法确认所有断续现象都由同一个原因引起。

## 4. 修改目标与非目标

### 4.1 修改目标

- 通知 TTS 开始发送音频前，设备必须建立当前播放所有权并完成可接收状态准备。
- 不允许因为退出屏保而丢失语音开头。
- 服务端只有收到当前连接、会话和句子严格匹配的 `done` 才认为可靠播放成功。
- 失败时完整重播提醒，不从不可信的中间音频位置续播。
- 旧连接、旧句子、旧 generation 和旧 PCM 不得影响新会话。
- 播放完成或失败后恢复正确的设备状态，不错误开启麦克风。
- 目标 Waveshare 1.46 音频输出必须具有有限等待上界和可报告的错误原因。

### 4.2 非目标

- 不在本次更新中证明屏幕刷新是 CPU 不足的根因。
- 不保证所有非目标开发板和音频 codec 都具有同样的有限底层写入上界。
- 不把可靠投递状态持久化到服务进程重启之后。
- 不引入 UDP `final_sequence`、固定 180 ms 尾部等待或其他猜测式完成机制。

## 5. 修改前后对比

| 场景 | 修改前 | 修改后 | 用户影响 |
|---|---|---|---|
| 屏保状态收到提醒 | 切换界面和音频发送可能并行 | 设备先建立屏保退出和播放准备流程，再返回 `ready` | 降低开头丢失和切屏期间播放异常风险 |
| 音频发送时机 | `tts:start` 后即可开始发送 | 服务端等待严格匹配的 `ready` 后才发送 | 首包不再依赖设备准备速度 |
| 成功判定 | 发送结束不能证明设备播完 | 当前句子进入 `DONE_WAIT` 后收到匹配 `done` 才成功 | 提醒不会因为“发过了”而被错误完成 |
| 连接中断 | 可能丢失或中止提醒 | 释放当前 attempt，等待替代连接并整句重试 | 临时断线后提醒仍可播报 |
| 旧音频与迟到 ACK | 可能污染或错误完成新会话 | connection/session/sentence/generation/epoch 多层隔离 | 降低串音、尾包和错误完成 |
| 通知连续抢占 | 返回状态可能被旧 close 或新 start 覆盖 | notification origin token 与 return state 继承 | 播完后回到正确状态，不错误监听 |
| 音频输出卡住 | I2S 写入可能无限等待 | Waveshare 写入有限超时并报告 `output_write_timeout` | 卡死变为可观察、可重试错误 |

## 6. 系统流程变化

### 6.1 修改前

```text
通知到达
→ 服务端发送 tts:start
→ 服务端开始发送音频
→ 设备同时处理屏保、界面和音频准备
→ 服务端发送结束
→ 无法严格确认设备是否完整播完
```

### 6.2 修改后

```text
Doorbell MQTT 通知/唤醒
→ WebSocket tts:start
→ 设备建立句子所有权、退出屏保并准备音频链路
→ 设备返回 ready
→ 服务端通过同一 WebSocket/TCP 发送二进制音频
→ 设备解码、重采样并排空输出
→ 设备返回 done 或带 reason 的 error
→ 服务端完成投递，或更换 sentence_id 后整句重试
```

## 7. 详细修改内容

### 7.1 服务端

- 引入 `READY_WAIT → STREAMING → DONE_WAIT → TERMINAL` 的显式 TTS attempt 生命周期。
- `ready` 只在 `READY_WAIT` 接受；过早 `done` 不缓存，也不能提前完成当前句子。
- `error` 可以在任意活动非终态终结 attempt，包括音频仍处于 `STREAMING` 时。
- 同一设备的可靠 TTS 从获取连接到终态结果之间使用 attempt lease 串行化，不同设备仍可并发。
- 可靠开始前取消并等待旧音频 sender，清理旧队列，并在每次二进制发送前验证句子所有权。
- ACK 历史 TTL 只清理 `TERMINAL` 历史，不回收仍活动的 `READY_WAIT`、`STREAMING` 或 `DONE_WAIT`。
- 连接关闭使用 typed error 唤醒等待者，不通过取消 Future 杀死 dispatcher 重试任务。
- 已经收到 exact `done` 后即使连接立即关闭，也只产生一次成功回调，不会误报失败或创建新句子。
- Doorbell wake、设备注册表和连接暂时不可用时继续按配置退避重试。
- 可靠能力判断要求 `tts_ready_ack`、`tts_done_ack`、`tts_preroll_buffer` 三个字段都严格为 JSON boolean `true`。

### 7.2 固件

- 使用 notification-origin token 绑定通知打开意图，旧 channel close 不能清除新通知意图。
- 通知 A 被通知 B 抢占时继承正确 return state；旧 generation 不能恢复新会话状态。
- 使用 TTS generation 和 audio pipeline epoch 隔离 reset 前后的 decode、queue 和 output 工作。
- pre-roll 在设备准备阶段有序保存提前到达的音频；溢出时返回 `preroll_overflow`，不静默丢包。
- decoder、decode、resampler 和 output 失败都转换为 generation-scoped typed error。
- `AudioCodec::OutputData()` 返回真实成功状态，只有完整写入成功才更新输出完成计数和时间线。
- Waveshare 1.46 的 `NoAudioCodec` I2S 写入使用 1000 ms 有限超时；超过 1500 ms 输出 deadline、短写或驱动错误归类为 `output_write_timeout`。
- 播放失败在离开 output barrier 后报告，reset 不再被无限 I2S 写永久阻塞。
- pager 满槽时传播真实失败，使 Application 可以执行 transient notification fallback。
- legacy 无 `sentence_id` 音频保留 Speaking 状态检查和反向早返回，不进入可靠 ingress。

### 7.3 文档与合同

- 明确可靠播放能力、消息形状、ACK 匹配、错误原因、超时、重试和兼容边界。
- 明确 Doorbell MQTT 与应用 WebSocket/TCP 的职责分离，避免把 legacy MQTT/UDP 音频路径误认为当前部署链路。

## 8. 协议、配置与数据变化

### 8.1 协议变化

- 服务端控制消息继续使用 `type=tts`，可靠句子必须携带唯一 `sentence_id`。
- 设备在同一 WebSocket/TCP 连接返回 `ready`、`done` 或 `error`。
- ACK 使用当前 connection、session、`sentence_id` 和 phase 联合匹配。
- reminder card 使用稳定 `delivery_id`，卡片接收 ACK 与 TTS 播放完成是独立门槛。
- 新增和统一的 error reason 包括 `preroll_overflow`、`pipeline_reset_timeout`、`drain_task_create_failed`、`playback_drain_timeout`、`superseded`、`stale_start`、`decoder_create_failed`、`decode_failed`、`resampler_create_failed`、`output_write_timeout`。

### 8.2 配置变化

| 配置项 | 默认值 | 作用 |
|---|---:|---|
| `tts_ready_ack_timeout_ms` | 700 ms | 等待设备进入可接收音频状态 |
| `tts_done_ack_timeout_ms` | 10,000 ms | 等待设备确认输出排空 |
| `tts_ready_start_retry_delays_ms` | `[300, 600, 1200]` ms | 同一 attempt 的 start/ready 重试间隔 |
| `tts_delivery_retry_delays_ms` | `[2000, 5000, 15000, 30000]` ms | 可靠投递失败后的退避间隔 |

### 8.3 数据变化

- 没有数据库 schema 迁移。
- 没有新增跨服务进程重启的持久化投递状态。
- 运行时 delivery/attempt 状态仍位于服务进程内存和现有 delivery store 范围内。

## 9. 影响范围与兼容性

### 9.1 用户影响

- 通知语音更倾向于从完整句首开始，并在临时故障后整句重播。
- 极端故障下可能听到一次未完成片段，随后听到完整重播；这是避免提醒丢失的设计选择。
- 屏保会在可靠 TTS 准备阶段退出，播放后恢复通知来源对应的状态。

### 9.2 性能与网络

- 增加少量 ACK JSON、生命周期状态和重试日志。
- 同一设备的可靠 TTS 串行执行，避免并发音频互相污染。
- 失败重试会重复发送整句音频，网络开销高于静默丢弃，但只发生在失败路径。

### 9.3 兼容性

- 旧设备继续走 `legacy_unverified`，不宣称严格可靠播放。
- 强保证只对同时严格声明 `tts_ready_ack=true`、`tts_done_ack=true`、`tts_preroll_buffer=true` 的设备生效。
- Doorbell MQTT 不承载 TTS 二进制音频；固件 MQTT/UDP 音频代码属于未启用 legacy 路径。
- 当前有限输出写结论覆盖实际部署的 Waveshare 1.46 `NoAudioCodecSimplex`，不自动推广到所有 codec。

## 10. 部署与迁移说明

推荐顺序：

1. 备份当前服务端和固件可用版本及配置。
2. 先部署服务端提交 `5a99cd0aeb97fed2233717276d4f2d906f37742c`，确认旧固件仍可按 `legacy_unverified` 工作。
3. 使用 ESP-IDF v5.5.4 和 `CONFIG_BOARD_TYPE_WAVESHARE_ESP32_S3_TOUCH_LCD_1_46=y` 构建固件提交 `1b483480717bf333f4bef176e911d761834503d7`。
4. 刷入固件后确认 hello 中三个可靠能力字段都是 boolean `true`。
5. 执行屏保、省电、连续通知、WebSocket 断线、输出超时和播放后麦克风状态验收。
6. 观察 ready/done 延迟、错误原因和重试行为，确认没有旧 PCM 尾包或重复卡片。

服务端先更新可以保留旧设备兼容窗口；不能先把旧设备错误标记为可靠播放设备。

## 11. 验证结果

| 验证层级 | 结果 | 证据与范围 |
|---|---|---|
| 服务端自动化测试 | 通过 | 合并后的 `main`：`487 passed, 1 skipped`，另有 2 条既有环境 warning |
| 固件 Python 测试 | 通过 | 合并后的固件 `tests/`：`264 passed` |
| 固件 host tests | 通过 | 9 个相关原生测试重新编译并执行通过，覆盖 origin、epoch、completion、ownership、watchdog、state、validation 和 pager |
| 目标板构建 | 通过 | ESP-IDF v5.5.4 Waveshare 1.46 fullclean 构建；`ai_pet.bin` 5,673,808 字节；应用分区剩余 55% |
| 实机验收 | 未执行 | 当时没有串口/USB 设备，无法刷机并执行物理播放场景 |
| 生产验证 | 未记录 | 没有生产环境观测证据，不能用自动化或构建结果替代 |

## 12. 风险与已知限制

- 真实设备是否完全消除断续仍未知，需要同时采集 CPU 占用、I2S underrun、音频队列深度和屏幕刷新耗时。
- 可靠投递只保证服务进程生命周期内持续重试，服务进程重启后不恢复未完成 attempt。
- notification-origin token 在有序 WebSocket 正常路径中可靠；恶意或异常 stale/replayed start 消费新 intent 的理论边界仍属于低概率 Minor。
- 尚无 fake codec 驱动完整 `AudioService → Application → 单一 error ACK、无 done` 的行为级集成测试。
- 非目标 codec 的底层 write 可能仍使用不同的等待策略。
- timing 日志包含 `delivery_id` 和 `sentence_id`，但没有连接层无法准确生成的 retry attempt 编号。

## 13. 回滚方案

### 13.1 回滚目标

- 服务端回滚目标：`189fd7528cfced80d0c4dcca58afbbd9cb4a7165`。
- 固件回滚目标：`a5e0349d06ea394f5a4b0c76e5f51bfede12e12a`。

### 13.2 回滚顺序

1. 如果需要立即停止新可靠能力声明，先将设备刷回固件回滚目标。
2. 确认设备 hello 不再声明新的严格可靠能力。
3. 再将服务端回滚到服务端目标提交。
4. 恢复与目标提交匹配的配置文件。
5. 重新验证 legacy 通知、普通对话 TTS、Doorbell MQTT 唤醒和 WebSocket 音频路径。

回滚后将失去 ready/done 强确认、pre-roll、typed output error 和服务进程内持续可靠重试能力。

## 14. 运行观察项

| 观察项 | 用途 | 异常信号 |
|---|---|---|
| `delivery_id` | 关联同一次提醒投递 | 同一 ID 创建重复卡片或无法找到投递 |
| `sentence_id` | 关联单次 TTS attempt | 旧句子 ACK 完成新句子 |
| `start_to_ready_ms` | 观察屏保和音频准备耗时 | 持续接近或超过 700 ms |
| `done_wait_ms` | 观察输出排空和 ACK 延迟 | 持续接近或超过 10,000 ms |
| `output_write_timeout` | 识别 I2S 写入失败 | 连续出现或与屏幕刷新同步出现 |
| WebSocket close reason | 判断连接替换原因 | 播放期间频繁断开 |
| retry delay | 判断退避是否按配置执行 | 无延迟快速重试或停止重试 |
| terminal state | 确认一次 attempt 只有一个终态 | 同时出现 done 和 error，或 exact done 后重试 |

## 15. 后续事项

- 连接 Waveshare 1.46 实机完成屏保、省电、连续通知、断线、输出超时、pager 满槽和 reset 尾包验收。
- 在实机上采集 CPU、I2S、音频队列和屏幕刷新指标，判断剩余断续是否属于性能问题。
- 评估增加 fake codec 行为级集成测试。
- 根据运行数据决定是否补充 retry attempt 日志字段。
- 实机或生产验证完成后更新本文件的验证结果和文档修订记录。

## 16. 关联提交与文档

### 16.1 提交

- [服务端合并提交 5a99cd0](https://github.com/jiang-source-tech/xiaoxin-esp32-server/commit/5a99cd0aeb97fed2233717276d4f2d906f37742c)
- [固件合并提交 1b48348](https://github.com/jiang-source-tech/hzcu_xiaoxin_firmwire_private/commit/1b483480717bf333f4bef176e911d761834503d7)

### 16.2 文档

- [可靠通知 TTS 协议合同](../../development/xiaoxin-tts-playback-ack.md)
- [屏保唤醒与 pre-roll 设计](../../superpowers/specs/2026-07-10-notification-tts-screen-wake-preroll-design.zh-CN.md)
- [可靠通知 TTS 实施计划](../../superpowers/plans/2026-07-10-notification-tts-reliable-playback.md)

## 17. 文档修订记录

| 修订时间（UTC+8） | 修订内容 | 修订依据 |
|---|---|---|
| 2026-07-11 | 创建首条项目更新详情，记录服务端与固件合并结果、验证证据和未完成实机验收 | 合并提交、自动化测试、host tests 和 ESP-IDF 构建结果 |
