# 小芯 TTS 端到端诊断设计

## 目标

为小芯服务端与 ESP32 固件增加一组可关闭、低侵入、可关联的诊断指标，用一次真实播报明确音频断续发生在以下哪个边界：

1. Qwen 实时 TTS 音频生成；
2. PCM 到 Opus 编码；
3. 服务端发送节拍；
4. WebSocket 到设备的到达节拍；
5. ESP32 Opus 接收与入队；
6. Opus 解码、重采样和播放队列；
7. I2S 扬声器输出。

本轮只增加观测，不修改播放节拍、缓冲容量、采样率、线程优先级、音频后处理参数或 ACK 行为。

## 已知事实

- 服务端当前使用 Qwen 实时 TTS，音频帧按 60 ms Opus 帧发送。
- 服务端开头直接发送约 5 至 6 帧，之后由 `AudioRateController` 控制发送节拍。
- ESP32 的 Opus 接收队列上限约为 2.4 秒，PCM 播放任务队列上限为 2 帧。
- ESP32 当前未声明 `tts_ready_ack` 或 `tts_done_ack`。
- ESP32 只在 `kDeviceStateSpeaking` 状态接收下行音频，`tts start` 到 Speaking 的状态切换通过主任务异步调度。
- 目标板扬声器输出采样率为 24 kHz，但设备 hello 当前声明 16 kHz 音频参数。

## 方案选择

采用服务端与固件双端关联日志。

没有采用仅固件日志，因为它只能证明设备欠载，不能区分服务端停发与网络停顿。没有采用全量 Opus、PCM 和网络 trace 持久化，因为第一轮诊断不需要如此大的数据量，而且大规模落盘和串口输出可能改变音频实时性。

## 关联模型

每次 TTS 使用现有 `sentence_id` 作为会话关联键。服务端为每个句子维护从零开始递增的诊断帧序号。固件的 WebSocket v1 下行音频没有显式序号，因此固件使用本地接收计数作为设备侧序号，并通过 `tts start`、`sentence_id`、首包时间和帧数与服务端时间线对齐。

所有新增日志统一使用 `[TTS-DIAG]` 前缀。日志字段使用稳定的 `key=value` 格式，方便直接用 `rg`、脚本或表格解析。

## 服务端观测

### Qwen 音频到达

在处理 `response.audio.delta` 时记录：

- `sentence_id`
- delta 序号
- 单调时间
- 与前一个 delta 的间隔 `delta_gap_ms`
- 解码后的 PCM 字节数
- 当前 TTS 会话是否处于激活状态

首个 delta 总是记录。后续正常 delta 只做周期汇总；当间隔超过阈值时立即记录异常。

### Opus 产出

在 Qwen PCM 经 Opus 编码回调产生帧时记录：

- `sentence_id`
- Opus 帧序号
- 与前一帧的间隔 `opus_gap_ms`
- Opus 包字节数

该边界用于区分“Qwen delta 已及时到达，但编码没有及时产出”和“编码正常，后续发送发生停顿”。

### WebSocket 实际发送

在 `_do_send_audio` 完成 WebSocket `send` 后记录：

- `sentence_id`
- 服务端音频序号
- 发送完成单调时间
- 与上一包发送完成时间的间隔 `send_gap_ms`
- `AudioRateController` 队列深度
- 当前是否仍处于预发送阶段

异常分级：

- `send_gap_ms > 90`：`GAP`
- `send_gap_ms > 300`：`STARVATION_RISK`

90 ms 阈值允许 60 ms 音频帧存在一定调度误差；300 ms 对应当前初始预发送缓冲的大致安全边界。

### 服务端汇总

每个句子结束时输出一条汇总，包括：

- delta 数和最大 delta 间隔
- Opus 帧数和最大 Opus 产出间隔
- 已发送帧数和最大发送间隔
- `GAP` 数
- `STARVATION_RISK` 数

服务端诊断由配置项 `tts_diagnostics_enabled` 控制，默认关闭。关闭时不执行逐帧日志格式化。

## ESP32 观测

### TTS 状态切换

收到 `tts start` 时记录：

- `sentence_id`
- 收到 start 的单调时间
- 当前设备状态

主任务真正进入 `kDeviceStateSpeaking` 后记录：

- `sentence_id`
- 状态切换完成时间
- `start_to_speaking_ms`

若在 Speaking 完成前收到音频，记录 `AUDIO_BEFORE_SPEAKING` 和累计丢弃数。

### WebSocket 音频接收与入队

每个下行音频包记录或累计：

- 本地接收序号
- 与上一包的间隔 `recv_gap_ms`
- 包字节数
- 当前设备状态
- `PushPacketToDecodeQueue()` 返回值

异常立即输出：

- `recv_gap_ms > 90`：`GAP`
- `recv_gap_ms > 300`：`STARVATION_RISK`
- 入队失败：`DECODE_QUEUE_FULL_DROP`
- 非 Speaking 状态丢弃：`STATE_DROP`

正常包每 20 帧输出一次汇总，避免串口逐帧输出改变实时调度。

### 解码与播放队列

`AudioService` 增加只读诊断快照，包含：

- `audio_decode_queue_` 深度
- `audio_playback_queue_` 深度
- 解码成功数和失败数
- 入队丢弃数
- 播放数
- 播放队列从非空变为空的次数

在以下事件立即输出：

- TTS 仍处于 Speaking 且解码队列和播放队列同时为空：`PLAYBACK_STARVED`
- 单帧 Opus 解码耗时超过 60 ms：`DECODE_OVERRUN`
- 重采样加解码总耗时超过 60 ms：`PIPELINE_OVERRUN`

### I2S 输出

在 `codec_->OutputData()` 前后测量单调时间，记录：

- PCM 样本数
- 音频覆盖时长
- EQ、DRC、limiter 处理耗时
- `OutputData` 调用耗时
- 播放队列深度

处理或写入耗时超过当前帧覆盖时长 60 ms 时立即记录 `I2S_OVERRUN`。正常帧只累计最大值，在句子结束时汇总。

固件诊断通过 Kconfig 开关 `CONFIG_XIAOXIN_TTS_DIAGNOSTICS` 控制，默认关闭。启用后仍对正常路径限频，异常事件不限频。

## 数据流判定

一次播报结束后，按以下规则归因：

| 观测结果 | 主要责任边界 |
| --- | --- |
| Qwen delta 已出现长间隔，后续边界同步出现长间隔 | TTS 供应商或服务端到 Qwen 的网络 |
| delta 连续，Opus 产出出现长间隔 | PCM 缓冲或 Opus 编码 |
| Opus 连续，WebSocket 发送出现长间隔 | 服务端事件循环或发送控制器 |
| 服务端发送连续，ESP32 接收出现长间隔 | 网络或设备 WebSocket 接收调度 |
| ESP32 接收连续，但出现入队失败 | 固件解码队列背压或消费不足 |
| 接收和入队连续，但出现 `PLAYBACK_STARVED` | 解码、重采样或播放任务调度 |
| 播放队列有数据，但出现 `I2S_OVERRUN` | 音频后处理、I2S 驱动或硬件输出路径 |
| 只出现 `AUDIO_BEFORE_SPEAKING` | TTS 起播状态切换和 ready ACK 缺失 |

## 实机采集流程

使用同一个已绑定设备，保持网络位置和服务器部署不变，依次执行：

1. 播放本地 OGG 提示音，用于验证解码、后处理和 I2S 基线；
2. 通过控制台下发一条约 10 至 15 个汉字的短 TTS；
3. 下发一条约 80 至 120 个汉字的长 TTS；
4. 对长 TTS 重复两次，共取得三条云端 TTS 样本；
5. 保存服务端日志和 ESP32 串口日志；
6. 按 `sentence_id`、起止时间、帧数与异常标记合并时间线。

本轮成功标准不是“声音已经修好”，而是三条云端样本中至少一条复现断续，并能把第一个异常边界唯一定位到上述七个边界之一。如果三条均未复现，则保留诊断版本，增加文本长度或网络压力后继续采集，而不是根据无异常样本下结论。

## 测试策略

### 服务端

- 单元测试诊断状态在禁用时不产生日志数据。
- 单元测试 60 ms 正常节拍不标记 GAP。
- 单元测试 120 ms 间隔标记 GAP。
- 单元测试 350 ms 间隔标记 STARVATION_RISK。
- 单元测试句子切换后计数和最大间隔重置。
- 使用现有发送代码的异步测试验证日志记录的是实际 WebSocket 发送完成时间。

### 固件

- 为纯函数阈值判定和汇总计数添加主机侧测试。
- 路径测试确认 hello、TTS JSON 和音频协议在诊断开关关闭时保持原样。
- 编译诊断开关关闭和开启两个配置。
- 实机验证日志限频，没有因逐帧串口输出制造新的播放卡顿。

## 错误处理与清理

- 诊断计数器溢出时使用饱和或自然回绕，不影响音频路径。
- 缺失 `sentence_id` 时使用当前 TTS 代际和本地序号记录，不拒绝音频。
- 日志失败、格式化失败或诊断状态不可用时跳过诊断，不改变播放结果。
- 找到根因后删除不再需要的逐边界临时日志；保留低成本的异常汇总指标和队列丢弃告警需要另行评审。

## 非目标

- 本轮不增加 jitter buffer。
- 本轮不调整 `PRE_BUFFER_COUNT`。
- 本轮不实现 TTS ready/done ACK。
- 本轮不修改 Qwen 模型、音色、音量或指令。
- 本轮不修改 ESP32 EQ、DRC、limiter、音量增益或 I2S DMA 参数。
- 本轮不自动上传 PCM、Opus 或串口日志。
