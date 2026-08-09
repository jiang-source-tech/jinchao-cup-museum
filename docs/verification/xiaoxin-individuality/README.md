# 小芯个体成长验证台账

本目录保存可复核的验证合同、证据模板和实际运行报告。代码存在不代表验证通过；所有门禁只输出 `PASS`、`FAIL` 或 `INCONCLUSIVE`。

## 自动化矩阵

在 `main/xiaozhi-server` 目录执行：

```powershell
..\..\.venv\Scripts\python.exe scripts/xiaoxin_individuality_gate.py matrix `
  --replays 20 `
  --output ..\..\docs\verification\xiaoxin-individuality\runs\matrix-report.json
```

矩阵直接调用生产 `build_companion_policy`，覆盖 243 种气质、90 组双轴档位、七类探针、七类场景、七类控制状态，以及同输入至少 20 次确定性重放。双轴门禁会验证目标轴确实影响对应风格维度，并拒绝额外的交互副作用。

## 研究合同

```powershell
..\..\.venv\Scripts\python.exe scripts/xiaoxin_individuality_gate.py research-contract `
  --output ..\..\docs\verification\xiaoxin-individuality\runs\research-contract.json
```

`contract_hash` 覆盖完整预注册 JSON 和数据字典，包括分组、排除规则、版本字段、样本门槛、D7/D30/D90 阈值及至少 10,000 次参与者聚类 bootstrap。参与者 2AFC 任务与答案键分离，任务对象不包含正确位置。

真实研究数据使用：

```powershell
..\..\.venv\Scripts\python.exe scripts/xiaoxin_individuality_gate.py research-results `
  --responses <responses.jsonl> `
  --assignments <assignments.json> `
  --output <research-report.json>
```

收集未结束时不要添加 `--collection-complete`。缺样本、缺检查点或缺轴档位会得到 `INCONCLUSIVE`；收集已经冻结结束后，同类缺口才判为 `FAIL`。

## 真实 ESP32 HIL

每次真机运行使用独立证据目录：

```text
hil-run/
  manifest.json
  capture-attestation.json
  events.jsonl
  serial.jsonl
  server.jsonl
  network.jsonl
```

先确认目标 ESP32 已连接。默认采集器通过普通复位读取固件启动指纹、USB 实例、设备 MAC/UUID、固件版本和实际 OTA HTTP 端点，并保存同名 `.serial.log` 原始证据及其 SHA-256：

```powershell
..\..\.venv\Scripts\python.exe scripts/xiaoxin_individuality_gate.py hil-attest `
  --serial-port COM7 `
  --server-host <candidate-server> `
  --server-port <port> `
  --output <hil-run>\capture-attestation.json
```

每台设备分别执行一次并使用不同的输出文件；第二台及后续设备写入 manifest 的 `additional_serial_ports` 和 `additional_capture_attestations`。完整门禁要求每个绑定设备都有唯一 USB 实例证明，且服务端结构化日志同时匹配该设备的 MAC、UUID、固件版本和候选服务器 Git SHA。OTA HTTP 端点证明不替代 WebSocket 与业务事件日志，OTA 成功/回滚和重连路径仍必须由同 event id 的 `server.jsonl` 与 `network.jsonl` 记录关联。

支持 challenge 协议的固件可显式添加 `--attestation-method serial_challenge`；响应必须回传 MAC、UUID、固件版本、项目名和 ELF SHA-256/前缀，原始响应会保存为同名 `.challenge.json` 并在 finalize 时重算摘要。需要被动观察且不能复位设备时添加 `--no-reset-device`，但日志窗口内缺少启动身份字段会保持 `INCONCLUSIVE`。

完成事件和三路结构化日志采集后，先把真机身份证明与完整采集流绑定：

```powershell
..\..\.venv\Scripts\python.exe scripts/xiaoxin_individuality_gate.py hil-finalize-attestation `
  --bundle <hil-run>
```

然后执行门禁：

```powershell
..\..\.venv\Scripts\python.exe scripts/xiaoxin_individuality_gate.py hil `
  --bundle <hil-run> `
  --output <hil-run>\report.json
```

正式 `PASS` 必须同时满足：

- 真实硬件采集证明覆盖完整运行窗口，串口、服务端日志流和网络采集均成功；
- 至少两个唯一设备与两个唯一主体，且 device-subject-pet-epoch 绑定固定；
- 每个事件以结构化字段同时关联串口和服务端日志，网络路径还必须关联网络日志；
- 冷启动、对话、重启、重连、重复/延迟投递、恢复供电、控制、隔离和 OTA 等每条关键路径至少 30 次；
- 身份串用、重复表达、旧状态复活和错误控制结果为零；
- `stability_24h` 事件自身覆盖至少 24 小时，相邻采样不超过 30 分钟；
- 使用已经冻结的真实设备 SLO，且每个 SLO 都有测量值。

模板默认 `evidence_origin=synthetic` 且 `synthetic=true`。修改布尔值不能替代真实串口启动观测或 challenge、三路结构化日志；没有两台开发板、冻结 SLO、关键路径 30 次证据或完整 24 小时窗口时，结果只能是 `INCONCLUSIVE`。

## 数据纪律

- HIL 只使用隔离测试主体和合成场景，不使用生产用户私人数据。
- 30 次零失败只表示本次回归门禁通过，不表示生产可靠率。
- 截图和视频只能作为补充，不能替代结构化事件与日志。
- D7、D30、D90 尚未真实发生时，不创建伪造结果或提前标记通过。
