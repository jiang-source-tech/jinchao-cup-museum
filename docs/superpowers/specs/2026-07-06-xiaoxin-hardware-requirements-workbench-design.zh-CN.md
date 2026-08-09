# Xiaoxin 硬件端需求分栏设计

日期：2026-07-06

## 背景

`docs/requirements/requirements.yaml` 已经是 Xiaoxin 项目状态工作台的事实源。它目前包含服务端、小程序、产品路线、固件条目和证据，但硬件端信息主要散落在 `items` 里的 firmware 条目中，例如宠物主页、通知中心、Overview、本机设置和低功耗。

这种结构会导致一个判断偏差：服务端和小程序需求能按分栏阅读，但硬件端只能在长列表里搜索。后续讨论“基本功能是否完成”时，容易把“服务端接口已存在”误判成“硬件体验已闭环”。硬件项目尤其不能这么判断；没有 build、flash、OTA、WebSocket/MQTT 联调、ACK、长时间运行和真机验收，状态不能轻易标为完成。

## 目标

新增一个 `hardware_requirements` 顶层分栏，让硬件端和 `mini_program_requirements` 一样拥有独立路线图。

这个分栏同时表达两类信息：

- 产品体验：宠物主页、通知、Overview、设置、低功耗、播报、陪伴状态。
- 工程交付：OTA、WebSocket、MQTT、ACK、音频播放完成、数据注入、离线恢复、build/flash、真机验收。

## 非目标

本设计不直接修改固件代码。

本设计不把固件仓库的功能地图原样搬进服务端仓库。服务端需求工作台仍然是总账，只吸收硬件端的产品和验收状态。

本设计不拆分 `requirements.yaml`。当前文件仍可维护，先新增顶层分栏；只有当单文件明显过大时，再考虑拆成多个 YAML 后聚合。

## 结构

在 `mini_program_requirements` 同级新增：

```yaml
hardware_requirements:
  title: 硬件端需求分栏
  summary: 硬件端是小芯 AI Pet 的主要陪伴入口，必须同时跟踪产品体验和工程验收。
  current_state:
    - 已有宠物主页、通知中心、Overview、设置页、低功耗和运行健康等 firmware 条目。
  recommendation: 先保证连接、绑定、投递和 ACK，再推进通知、Overview、宠物状态和长稳验收。
  priority_order:
    - HW-00 设备连接、OTA 与绑定闭环
    - HW-01 语音播放、播报完成与 ACK
    - HW-02 通知中心与 heads-up 提醒
    - HW-03 Overview 总览页真实数据
    - HW-04 宠物主页与轻量状态
    - HW-05 本机设置、配网与设备信息
    - HW-06 低功耗、电池与运行健康
    - HW-07 真机发布、烧录、OTA 与长稳验收
  columns:
    - id: HW-00
      title: 设备连接、OTA 与绑定闭环
      priority: P0
      status: partial
      focus: 让设备稳定连接私有服务端，并能绑定到学生账号。
      implemented:
        - 已有 Xiaoxin 私有 OTA 与 WebSocket 路径。
      requirements:
        - 设备请求 OTA 后获得正确 WebSocket 地址和激活信息。
      remaining:
        - 仍需一次真实固件 OTA 与绑定验收记录。
      acceptance:
        - 真机可以通过私有 OTA 地址连接 `/xiaoxin/v1/`。
      related_items:
        - XIAOXIN-002
```

字段沿用小程序分栏的形态，便于 HTML 渲染器复用现有展示逻辑：`implemented` 表示已有证据，`requirements` 表示目标行为，`remaining` 表示未完成缺口，`acceptance` 表示验收标准，`related_items` 关联已有 `XIAOXIN-*` 或 `XIAOXIN-PROD-*` 条目。

## 分栏定义

### HW-00 设备连接、OTA 与绑定闭环

覆盖设备从刷入固件、请求 `/xiaoxin/ota/`、拿到 WebSocket 地址、获得激活码、绑定到学生账号、后续稳定连接私有服务端的闭环。

工程验收必须包含真实 OTA 响应、设备 ID 稳定性、绑定码展示或播报、WebSocket 地址一致性，以及至少一次真机连接记录。

### HW-01 语音播放、播报完成与 ACK

覆盖服务端 TTS 音频下发、设备播放、播报完成识别、`xiaoxin_ack` 回传和投递状态推进。

工程验收必须区分 `device_received`、`speaking`、`done`、播放中断、播放失败和服务端兜底完成态。没有播放完成证据的投递不能直接标成可靠完成。

### HW-02 通知中心与 heads-up 提醒

覆盖课程提醒、普通提醒、系统状态、低电量、Wi-Fi 异常、OTA 状态和失败提示在设备上的通知呈现。

工程验收必须包含通知去重、更新、清理、过期、优先级、heads-up 显示，以及通知 ID 与服务端 delivery ID 的对应关系。

### HW-03 Overview 总览页真实数据

覆盖天气、课程、待办、设备状态、时间、网络、电量等摘要卡片。

工程验收必须证明服务端真实数据可以刷新硬件 Overview，而不是只在小程序或服务端 API 中存在。离线、未绑定、无课程、无提醒时必须显示明确空态。

### HW-04 宠物主页与轻量状态

覆盖宠物 GIF、情绪映射、触摸、摇晃、低电量、Wi-Fi 异常、长时间无互动、轻量亲密度或能量状态。

工程验收必须证明事件不会造成动画抖动、状态冲突或过度打扰。长期状态第一版可以轻量，但必须有本地或服务端事实来源。

### HW-05 本机设置、配网与设备信息

覆盖 BOOT 长按入口、亮度、Wi-Fi 重新配网、关于页、省电开关、音量或静音等设备本地设置。

工程验收必须包含设置持久化、重启后恢复、错误配网状态提示，以及和低功耗策略的冲突处理。

### HW-06 低功耗、电池与运行健康

覆盖电量 ADC、充电状态、低电告警、低电关机、自动息屏、低功耗时钟、唤醒、重启原因和运行健康诊断。

工程验收必须包含长时间真机运行记录。低功耗和电池类需求不能只靠模型测试完成。

### HW-07 真机发布、烧录、OTA 与长稳验收

覆盖固件 build、flash、OTA 升级、版本记录、固件包路径、设备日志、长稳运行和发布检查清单。

这是硬件端的 P0 护栏。每次硬件相关需求从 `partial` 升到 `done`，都应至少能指向一次真机验收记录或明确说明为什么该项只需要模型测试。

## 数据流

硬件端需求分栏不替代已有 `items`。它是按硬件体验组织的视图，已有 `items` 仍保存具体能力条目和证据。

典型关系如下：

```text
hardware_requirements.columns[HW-*]
  -> related_items
  -> XIAOXIN-002 / XIAOXIN-006 / XIAOXIN-007 / XIAOXIN-008 / XIAOXIN-009 / XIAOXIN-010 / XIAOXIN-011 / XIAOXIN-012
  -> evidence paths in server repo and firmware repo
```

服务端和小程序需求中的硬件依赖继续保留，但应通过 `related_items` 指向硬件分栏，避免重复写两套互相漂移的验收标准。

## 渲染影响

`requirements.html` 当前已经能展示顶层需求分栏时，应新增或复用一个区块显示 `hardware_requirements`。如果现有页面只特殊处理 `mini_program_requirements`，实现时应抽出通用的 requirements-section 渲染逻辑，同时渲染小程序和硬件两类分栏。

视觉结构不需要重新设计。硬件分栏应和小程序分栏保持一致，让用户能横向比较状态、优先级、已实现、剩余工作和验收标准。

## 错误处理与校验

`docs/requirements/server.py` 的 YAML 校验需要接受新的 `hardware_requirements` 顶层字段。

校验规则应至少覆盖：

- `hardware_requirements.columns` 必须是列表。
- 每个 column 必须有 `id`、`title`、`priority`、`status`。
- `status` 和 `priority` 必须使用 taxonomy 中已有枚举。
- `related_items` 如果存在，应引用已有 item id；第一阶段可只做警告，不阻断页面渲染。

如果硬件分栏缺失，页面仍可正常渲染；这保证旧版本 YAML 不会直接报错。

## 测试

需要补充三类验证：

- YAML 结构测试：`requirements.yaml` 能被 `server.py` 加载并通过校验。
- HTML 渲染测试：页面能显示硬件端标题、8 个 HW 分栏和每栏状态。
- 内容一致性检查：每个 HW 分栏至少关联一个已有 `items` 条目，避免新增孤立需求。

本设计不要求运行固件测试；真正实现硬件能力时，才需要在固件仓库补 build、flash 或模型测试证据。

## 推荐实施顺序

1. 在 `requirements.yaml` 增加 `hardware_requirements`。
2. 调整 `requirements.html`，把小程序分栏渲染抽象成通用分栏渲染。
3. 调整 `server.py` 校验逻辑，允许并校验硬件分栏。
4. 增加最小测试或手工校验，确认 `/requirements.json` 和 HTML 页面都能读取新字段。
5. 根据新增分栏回看已有 firmware items，必要时补 `related_items` 和验收证据。

## 完成标准

完成后，用户打开需求工作台时，能一眼看到硬件端当前处于什么状态：哪些体验已经有模型或真机证据，哪些只是服务端具备接口，哪些必须继续真机验证。

最重要的判断标准是：需求工作台不再把“服务端完成”误读成“硬件端完成”。
