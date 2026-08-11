# AC-010-4 固件协议与目标板构建验证记录

## 记录信息

| 项目 | 结果 |
| --- | --- |
| 验证日期 | 2026-08-11 |
| 需求 | REQ-010 / AC-010-4 |
| 固件仓库 | `D:\Learn\museum-firmwire` |
| 固件验证提交 | `f4e3291e778801c55ddd1c2b63abd2b1288ab4f6` |
| ESP-IDF | `v5.5.4` |
| 目标芯片 | `esp32s3` |
| 目标板 | `Waveshare ESP32-S3-Touch-LCD-1.46` |
| 固件 SHA-256 | `EFF64AD658CE99C81F1061E7B41E2DB312B935290B21F5231D0C747A51BCFACF` |
| 验证结论 | 协议测试通过，目标板固件构建通过 |

## 1. 协议冒烟测试

测试入口：

- `museum-firmwire/tests/museum_state_contract_test.py`
- `museum-firmwire/tests/museum_state_contract_test.cc`

Python 测试会用本机 `g++`/`gcc` 编译真实的 `main/museum_state.cc` 和 ESP-IDF 自带 `cJSON`，然后执行 C++ 合同测试。它不是只检查模拟对象或字符串快照。

执行命令：

```powershell
cd D:\Learn\museum-firmwire
New-Item -ItemType Directory -Path .pytest-local -Force
python -m pytest -q tests/museum_state_contract_test.py --basetemp=.pytest-local/museum-state-contract-final
```

结果：

```text
.                                                                        [100%]
1 passed in 2.33s
```

覆盖的可观察行为：

1. 合法 `grounded` 状态能够解析，依据数量能进入状态显示文本。
2. 合法 `missing_context` 状态能够解析，设备提示能进入状态显示文本。
3. 不支持的协议版本被拒绝，并返回 `unsupported_version`。
4. 未知的 `grounding.status` 枚举被拒绝，并返回 `invalid_grounding_status`。

## 2. 目标板构建

为避免 Windows 默认代码页导致 ESP-IDF 输出编码异常，使用进程级脚本策略和 UTF-8 控制台执行：

```powershell
cd D:\Learn\museum-firmwire
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '& { chcp 65001 > $null; . "D:\Espressif\frameworks\esp-idf-v5.5.4\export.ps1"; idf.py build }'
```

构建配置已确认：

```text
CONFIG_IDF_TARGET="esp32s3"
CONFIG_BOARD_TYPE_WAVESHARE_ESP32_S3_TOUCH_LCD_1_46=y
CONFIG_ESPTOOLPY_FLASHSIZE="16MB"
```

构建输出：

```text
Generated D:/Learn/museum-firmwire/build/ai_pet.bin
ai_pet.bin binary size 0x3db690 bytes.
Smallest app partition is 0x600000 bytes.
0x224970 bytes (36%) free.
Project build complete.
```

生成的应用固件：

- `D:\Learn\museum-firmwire\build\ai_pet.bin`
- 文件大小：`4,044,432` bytes
- SHA-256：`EFF64AD658CE99C81F1061E7B41E2DB312B935290B21F5231D0C747A51BCFACF`
- 应用分区：`0x600000`
- 剩余空间：`36%`

构建中存在三个未阻断的既有警告：`esp_video` 的 `_IO`/`_IOR`/`_IOW` 宏重复定义、`application.cc` 的未使用变量，以及目标板触摸坐标旧 API 的废弃提示。本轮没有把这些历史警告当作 AC-010-4 的协议问题，也没有顺手修改无关固件代码。

## 3. 验收边界

本记录可以证明：

- 服务端下发的 `museum_state` 合同在固件解析器中可执行；
- 合法和非法状态的边界行为已被测试锁定；
- 目标板选择、编译、链接和分区大小检查均通过。

本记录不能证明：

- 已经向真实设备刷写该固件；
- 真实屏幕确实显示了每一种状态；
- 真实麦克风、ASR、TTS、扬声器和 WebSocket 链路工作正常。

这些内容属于 `REQ-015` 的真机验收，不应倒灌到 AC-010-4 的结论中。

## 4. 变更规则

以后修改 `museum_state` 字段、版本或状态枚举时，必须按以下顺序更新：

1. 修改服务端/固件合同和协议测试；
2. 重新执行协议冒烟测试；
3. 重新构建 `esp32s3` 目标板；
4. 更新本记录的日期、提交号和构建输出；
5. 只有真实设备证据齐备后，才更新 `REQ-015`，不能仅凭本记录解除真机阻塞。
