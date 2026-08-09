# Xiaoxin OTA 优先发布决策图

**目标：** 先把远程固件升级做成安全、可控、可回滚、可审计的发布能力；之后用它完成真机功能验收。远程 OTA 不能取代第一台设备的一次 USB 引导刷机。

**硬事实（2026-07-12）：** 当前固件 `sdkconfig` 选择 `partitions/v2/16m.csv`；该分区表只有 `factory` 和 `assets`，没有 `otadata`、`ota_0`、`ota_1`。因此 `Ota::Upgrade()` 调用 `esp_ota_get_next_update_partition()` 时没有可写目标，当前远程二进制升级不能成功。服务端 OTA handler 已能按版本返回 URL，但 `main/xiaozhi-server/data/bin/` 当前没有可发布 bin，且默认 OTA 地址仍是 HTTP。固件会在**启动时**检查版本并自动尝试升级，不会在持续运行中自动轮询。

**发布策略建议：** 开发/金丝雀设备可自动安装；稳定设备只在空闲、联网稳定、满足电量或外接电源条件的维护窗口安装。不能把“每次提交”或“每个新 bin”直接推给全部设备；强制升级只用于明确标记的关键版本。

## #1: OTA 分区预算与 USB 引导版本

Blocked by: none
Type: Prototype

### Question

16 MB Flash 能否同时容纳两份可升级应用、`otadata`、NVS、PHY 和现有宠物资源，并为未来固件增长留下足够余量？

### Answer

先用目标板完整构建测量 app bin、资源分区和必要余量，再选定新的双 OTA slot 分区表；不能从旧的单 `factory` 表直接假定大小可行。刷写分区表前导出设备 MAC、绑定关系和必要 NVS 配置，不能假设分区迁移会保留设备状态。更新 `sdkconfig`、默认配置和分区表后，必须通过 USB 将这个“OTA 引导版本”刷入每台已有设备一次，因为当前布局无法远程创建 OTA slot。

完成条件：目标分区含 `otadata` 和两个 app OTA slot；`idf.py size` 证明每个 slot 的余量满足发布门槛；串口刷机和启动恢复通过。

## #2: 不可变发布物与版本清单

Blocked by: #1
Type: Prototype

### Question

服务端怎样确定“哪个设备、哪个版本、哪个 bin”可安全升级，而不是仅靠 `model_version.bin` 文件名扫描？

### Answer

建立不可变 release manifest：设备模型/板型、版本、Git SHA、构建时间、bin 大小、SHA-256、最小可升级版本、渠道（canary/stable）、发布状态、强制标记、下载 URL 和发行说明。发布物以版本目录保存，不允许覆盖同版本文件；`data/bin` 的旧扫描逻辑只保留为迁移兼容，不能作为生产事实源。

完成条件：服务端仅对匹配模型、渠道和版本规则的设备返回清单中的 artifact；manifest 与实际文件 SHA-256 一致且有自动测试。

## #3: HTTPS、真实性与下载完整性

Blocked by: #2
Type: Prototype

### Question

如何防止 HTTP 劫持、篡改 bin 或误把错误型号固件装到设备？

### Answer

正式 OTA endpoint 和下载 URL 必须使用稳定 HTTPS 域名，并验证服务端证书；设备在写入前后验证 manifest SHA-256、目标板/型号和镜像版本。当前 `CONFIG_SECURE_BOOT` 未启用，不能把 ESP 镜像格式校验误说成来源认证；评估并落实 Secure Boot V2/签名镜像的量产密钥方案。Flash encryption 是否启用是独立决策，不以它替代签名验证。

完成条件：HTTP OTA 在生产配置中被拒绝；错误证书、错误 hash、错误板型、降级版本都无法写入 boot slot，且有回归测试。

## #4: 设备端升级状态机与安全时机

Blocked by: #1, #2, #3
Type: Prototype

### Question

设备何时检查、何时下载、何时安装新版本，才能既自动又不打断用户？

### Answer

实现 `CHECK -> ELIGIBLE/DEFERRED -> DOWNLOAD -> VERIFY -> SET_BOOT -> REBOOT -> HEALTH_CHECK -> COMMITTED/ROLLBACK` 状态机。保留启动检查，并增加带抖动的周期检查或受控通知触发；升级只在无语音会话、无 TTS、网络稳定、空间足够且满足电量/充电策略时进行。UI 需显示“发现、下载、安装、成功、失败、推迟”而不把升级失败伪装成正常启动。

完成条件：设备长期在线时也会在预定周期发现新版本；不安全条件只推迟不下载；所有状态和原因可在设备与服务端查询。

## #5: 健康门槛与真正回滚

Blocked by: #1, #4
Type: Prototype

### Question

新固件何时才算成功，失败时如何回到旧版本？

### Answer

将 `esp_ota_mark_app_valid_cancel_rollback()` 从单纯的版本检查后移到实际健康门槛之后：设备需完成启动、Wi-Fi、OTA 配置读取、私有 WebSocket 连通和最小运行窗口。任何启动崩溃、健康超时或关键服务不可用都必须保留 pending 状态并触发 rollback；测试断电、下载中断、坏镜像、新版本启动失败和健康超时。

完成条件：日志可以证明成功提交或回滚到上一版本；失败设备不会无限重启，也不会误标新版本有效。

## #6: 发布控制面、灰度与审计

Blocked by: #2, #3, #4, #5
Type: Discuss

### Question

怎样避免一个错误 release 同时损坏全部设备？

### Answer

服务端按渠道和设备 allowlist 控制升级：先 1 台金丝雀，再小批量，再 stable；暂停和回退必须即时生效。保存每台设备的当前/目标版本、检查时间、下载、验证、重启、健康、提交或回滚结果。只在金丝雀成功且无异常后扩大范围。

完成条件：能在控制台或 API 查询发布批次与每台设备结果；同一 release 可暂停、撤回或回退，且不依赖人工翻日志判断。

## #7: 真机 OTA 闭环验收

Blocked by: #1, #2, #3, #4, #5, #6
Type: Prototype

### Question

如何证明 OTA 不只是代码存在，而是能可靠升级真实设备？

### Answer

先 USB 刷入 OTA 引导版本；再发布一个更高版本到 canary，记录设备 MAC、旧/新版本、manifest SHA、URL、下载进度、重启、WebSocket、Doorbell MQTT 配置和最终健康状态。重复一次失败版本或网络中断测试，确认 rollback。通过后才向 stable 设备放量。

完成条件：至少一条成功升级和一条可控失败/回滚记录进入真机台账；升级后仍连接 `/xiaoxin/v1/`、恢复通知/TTS/Overview。

## #8: OTA 后的完整硬件验收

Blocked by: #7
Type: Prototype

### Question

拥有可靠 OTA 后，如何完成余下的产品验收？

### Answer

使用 OTA 升级后的固件验证三类提醒、TTS 首句字幕、睡眠/唤醒、断网恢复、长稳和两设备隔离。把 delivery ID、截图/视频、broker 日志、Overview revision 和版本号归档到验收台账；只有此时 HW-07 才能从 `active` 改为 `done`。

完成条件：功能结果、运行版本和证据文件三者可以互相追溯。

## 后续边界

OTA 真机闭环完成后，才转入独立 P0：小程序体验版/正式版 HTTPS 合法域名、证书、反向代理、备案和微信 request 合法域名（XIAOXIN-PROD-018）。
