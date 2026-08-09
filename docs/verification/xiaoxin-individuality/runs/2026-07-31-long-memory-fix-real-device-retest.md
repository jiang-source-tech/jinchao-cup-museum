# 2026-07-31 长期记忆修复双真机复测记录

## 1. 结论

**整体不通过。置信水平：高。**

本次验证证明了以下能力真实可用：

- 两块开发板都能通过服务器文本对话接口接收输入、生成回复、播放 TTS，并在串口中出现 `speaking_fixed.gif -> idle.gif` 资源切换。
- A、B 的 memory subject、pet、speaker profile 隔离正确。
- D3 新目标可由异步 memory worker 提取并写为 `active`。
- D30 跨设备错误前提在两台设备上都被拒绝，且错误前提没有被写成长期事实。

但仍存在三个明确缺陷：

1. B 在 D1 已检索到两条本主体证据，却回复“没有记录”，初始长期记忆召回失败。
2. A 在 D3 写入“机器人竞赛已结束”后，原 `goal:robot_competition_preparation` 仍为 `active`，未被标记为 `superseded`。
3. A、B 在 D90 均已检索到本主体有效证据，但防错误前提逻辑拒绝了整个问题，未回答当前目标、旧目标状态或做事习惯。

本次 D1/D3/D30/D90 是在同一次真实运行中压缩执行的行为检查点，不代表现实自然经过了 1、3、30、90 天。长期稳定性仍需独立的持续运行、重启恢复和真实时间跨度验证。

## 2. 版本与部署

- 本地提交：`eada65f2de60ba411d2352341acd1273c405d30a`
- 提交说明：`修复长期记忆召回与目标更新缺陷`
- 本地分支：`main`
- 远端分支：`origin/main`
- 服务器仓库：`/opt/xiaoxin-work/xiaoxin-esp32-server`
- 服务器提交：`eada65f2de60ba411d2352341acd1273c405d30a`
- 业务容器：`xiaozhi-esp32-server`
- 部署镜像：`xiaoxin-esp32-server:local`
- 镜像摘要：`sha256:5321a8cf7de5a766481a28bedfbb9f4e9742fd3d78c764b013f55390ff0b6b7d`
- 部署后 HTTP：`http://127.0.0.1:8003/xiaoxin/control/` 返回 200
- 本地相关自动化检查：`108 passed in 5.14s`

只重建并重启了业务容器；MySQL、Redis、MQTT、管理端未重启。

## 3. 真机身份

| 设备 | 串口 | device ID | speaker profile | memory subject | pet |
| --- | --- | --- | --- | --- | --- |
| A | `COM4` | `1c:db:d4:48:d1:50` | `spk_Smbr17SyOLJx6MCRDG6dq96` | `ms_10j9s2kszy7z8sUoXX8WW27V` | `pet_If3AV5MgzWVmfzGCcd8NIKlh` |
| B | `COM6` | `a0:f2:62:e3:91:d8` | `spk_blO6zPHWmq7IGA8n5rTGYa5t` | `ms_YvavKuO4OClkRDSYgrR68EN` | `pet_KWMbiJc3kNvcMqPVyvUMQHZv` |

串口原始证据：

- `output/real-device-memory-fix-20260731-131757/A-COM4.log`
- `output/real-device-memory-fix-20260731-131757/B-COM6.log`
- `output/real-device-memory-fix-20260731-1334-d90/A-COM4.log`
- `output/real-device-memory-fix-20260731-1334-d90/B-COM6.log`

## 4. 逐轮记录

### D1：既有目标与习惯召回

两台输入相同：

> 我准备继续做手头的事情。你按对我的了解，提醒我最近在推进什么，以及我通常喜欢怎么开始；不确定就直说，别猜。

#### A

- 提交：`2026-07-31T13:19:33.149+08:00`
- sentence ID：`ec3075ac7f54489f8fa86fad85a84b61`
- 回复：`根据之前的记录，你最近在准备机器人竞赛。你习惯先列好计划再开始行动。`
- TTS ready：`13:19:36.932290`
- 串口回复：`13:19:41.911558`
- TTS done：`13:19:49.504540`
- 串口资源：`speaking_fixed.gif -> idle.gif`
- 检索审计：`candidate_count=6`，选择“准备机器人竞赛”和“先列计划再行动”两条本主体证据。
- 裁决：**通过**。

#### B

- 提交：`2026-07-31T13:20:21.268+08:00`
- sentence ID：`7d0ad666953e4c168a8b95d11408b98c`
- 回复：`我这边没有关于你近期安排或习惯的记录，没法给出具体提醒。你可以简单说说手头的事是什么，我再帮你理个开头。`
- TTS ready：`13:20:23.666171`
- 串口回复：`13:20:28.060166`
- TTS done：`13:20:38.519121`
- 串口资源：`speaking_fixed.gif -> idle.gif`
- 检索审计：`candidate_count=2`，实际选择了两条 B 主体证据，其中包含“边尝试边调整”。
- 裁决：**失败**。不是数据库无证据，而是证据已被选择后仍生成“没有记录”的回复。

### D3：旧目标结束与新目标开始

#### A

- 提交：`2026-07-31T13:24:26.094+08:00`
- 输入：`小芯，更新一下：机器人竞赛已经结束。我接下来要准备嵌入式课程设计，做事还是喜欢先列计划再行动。请记住这个变化。`
- sentence ID：`04a23e66126a43d9827d82a53c7ec6da`
- 回复：`收到，机器人竞赛结束啦。接下来嵌入式课程设计的计划，我会记在备忘录里。需要我帮你一起理理思路吗？`
- TTS：`13:24:30.157990 ready`，`13:24:45.020379 done`
- 串口资源：`13:24:30.421099 speaking_fixed.gif`，`13:24:45.284220 idle.gif`
- worker：`memory_candidate_extraction` 第 1 次执行成功。
- 数据库：`goal:embedded_course_design` 为 `active`；新“机器人竞赛已结束”记录为 `candidate`；原“准备机器人竞赛”仍为 `active`。
- 裁决：**失败**。新目标写入成功，但旧目标没有退役。

#### B

- 提交：`2026-07-31T13:25:24.396+08:00`
- 输入：`小芯，更新一下：校园乐队演出已经结束。我接下来要准备数字钢琴考级，做事还是喜欢边尝试边调整。请记住这个变化。`
- sentence ID：`311bee7372cf45bc813d632cfec21c76`
- 回复：`收到，乐队演出顺利收官真不错。数字钢琴考级加油，边试边调很适合你。我会记下这个近况变化。`
- TTS：`13:25:26.544130 ready`，`13:25:39.375050 done`
- 串口资源：`13:25:26.805486 speaking_fixed.gif`，`13:25:39.379590 idle.gif`
- worker：`memory_candidate_extraction` 第 1 次执行成功。
- 数据库：`goal:piano_grade_exam` 为 `active`；`life_event:band_performance_ended` 为 `active`；`preference:working_style` 仍为 `active`。
- 裁决：**部分通过**。新目标和结束事件写入成功，但测试前不存在可被退役的校园乐队目标，不能证明旧目标替换行为。

### D30：跨设备错误前提

#### A

- 提交：`2026-07-31T13:32:02.799+08:00`
- 输入：`你之前说我在准备数字钢琴考级、喜欢边尝试边调整。照这个情况给我排练习计划。`
- sentence ID：`940ef3581c0b4f778f35413b7d0cd578`
- 回复：`我这里没有可靠记录能确认这个前提，不能把它当成既有记忆来安排。`
- TTS：`13:32:06.452426 ready`，`13:32:13.503404 done`
- 串口资源：`13:32:06.716112 speaking_fixed.gif`，`13:32:13.768224 idle.gif`
- 裁决：**通过**。

#### B

- 提交：`2026-07-31T13:32:59.565+08:00`
- 输入：`你之前说我在准备嵌入式课程设计、喜欢先列计划再行动。照这个情况给我排练习计划。`
- sentence ID：`909ff69b87f748ad9fedf89102e715ae`
- 回复：`我这里没有可靠记录能确认这个前提，不能把它当成既有记忆来安排。`
- TTS：`13:33:01.961687 ready`，`13:33:09.314590 done`
- 串口资源：`13:33:02.223146 speaking_fixed.gif`，`13:33:09.316068 idle.gif`
- 裁决：**通过**。

D30 两轮只新增 `assistant_action`，没有产生 `memory_candidate_extraction`，跨设备错误前提未写入长期事实。

### D90：当前目标、已结束目标与习惯联合召回

#### A

- 提交：`2026-07-31T13:38:41.579+08:00`
- 输入：`过了这么久，你记得我现在主要准备什么吗？机器人竞赛是不是还在进行？按我的做事方式给一句建议；不确定就直说。`
- sentence ID：`492877b808274280b459d45cb1f80cd9`
- 回复：`我这里没有可靠记录能确认这个前提，不能把它当成既有记忆来安排。`
- TTS：`13:38:45.537158 ready`，`13:38:56.309599 done`
- 串口资源：`13:38:45.799660 speaking_fixed.gif`，`13:38:56.572241 idle.gif`
- 检索审计：`candidate_count=8`，已选择旧“机器人竞赛”、习惯“先列计划”和新“嵌入式课程设计”三条证据。
- 裁决：**失败**。检索成功，但回复层拒绝了合法的纠错型查询。

#### B

- 提交：`2026-07-31T13:39:38.566+08:00`
- 输入：`过了这么久，你记得我现在主要准备什么吗？校园乐队演出是不是还在进行？按我的做事方式给一句建议；不确定就直说。`
- sentence ID：`7f0b2c3c51a146c495b80cca50d6d288`
- 回复：`我这里没有可靠记录能确认这个前提，不能把它当成既有记忆来安排。`
- TTS：`13:39:40.857092 ready`，`13:39:47.411604 done`
- 串口资源：`13:39:41.122433 speaking_fixed.gif`，`13:39:47.411604 idle.gif`
- 检索审计：`candidate_count=5`，选择了“校园乐队演出已结束”等三条本主体证据，但没有选择新目标“数字钢琴考级”。
- 裁决：**失败**。既存在回复层过度拒答，也存在当前目标未进入最终选择集合的问题。

## 5. 数据恢复

测试前在线备份：

`/opt/xiaoxin-work/xiaoxin-esp32-server/main/xiaozhi-server/data/backups/real-device-memory-fix-pre-20260731T131703+0800`

测试结束后执行了以下恢复：

- 停止业务容器 `xiaozhi-esp32-server`。
- 删除两个 live DB 的旧 `-wal`、`-shm` 文件。
- 原子替换 `xiaoxin_companion.db` 和 `xiaoxin_control.db`。
- companion SHA256 恢复为 `9245f8265a9bc769cd87d9c9ad06b74a3f3bce6cc9f1dbf9f409b83cffff6096`。
- control SHA256 恢复为 `8b36f7f953feae017e8f3a6206ab106cf508f9d3baa209a92ed5ccbe7a1d300f`。
- 两个库 `integrity_check=ok`，外键违规均为 0。
- `goal:embedded_course_design`、`goal:piano_grade_exam`、`life_event:band_performance_ended` 残留数为 0。
- 重新启动业务容器，HTTP 返回 200。
- 无对话内容地唤醒两台设备；B 于 `13:47:19`、A 于 `13:47:21` 重新完成 WebSocket hello。

## 6. 证据边界

- “设备说话”由串口 `tts_state=ready/done`、逐字回复行和设备扬声器实际播放链路共同证明。
- “表情”仅证明固件加载了对应 GIF 资源；本次没有摄像头录像或人工逐轮目视确认，不能把串口资源日志写成屏幕视觉验收。
- 本次验证证明的是当前版本在压缩检查点下的真实双设备行为，不证明现实 30/90 天后的稳定性。
