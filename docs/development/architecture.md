# 系统架构

本文从小芯二次开发视角梳理继承来的 `xiaozhi-esp32-server` 代码结构。

小芯当前不是单纯部署小智后端，而是在小智 ESP32 语音 AI 服务基础上做产品化改造：服务端负责语音链路、模型编排、控制台、投递、身份和记忆；固件端负责显示、播放、触摸、通知、总览页和低功耗体验。

## 运行模块

### `main/xiaozhi-server`

Python 语音运行时。它负责 ESP32 WebSocket 服务、OTA HTTP 接口、模型编排、VAD/ASR/LLM/VLLM/TTS provider 加载、插件调用，以及大部分语音交互主循环。

修改以下能力时优先看这里：

- WebSocket 连接行为。
- OTA 响应行为。
- 模型 provider 选择和初始化。
- 工具、插件和函数调用。
- 单服务模式下的人格 prompt。
- Xiaoxin runtime、控制台投递和统一的 CompanionMind 陪伴记忆。

### `main/digital-human`

浏览器测试和数字人相关能力。它可以辅助测试音频交互，但不是小芯当前核心运行时。

## 第一阶段开发边界

第一阶段优先改配置、文档和 Xiaoxin 自己的薄层能力，再改大结构。

高价值定制点：

- 模型 provider 配置。
- 人格 prompt。
- TTS 音色。
- 唤醒词。
- OTA/WebSocket 运行路径。
- 管理控制台中文文案。
- `8003/xiaoxin/control/` 小芯控制台。
- CompanionMind 陪伴记忆和身份隔离。

## 陪伴记忆 V2 架构

服务端只保留一个深模块 `CompanionMind` 作为记忆 interface：

```text
语音 runtime / 控制台 / 后台 worker
                |
                v
        CompanionMind 六个操作
 prepare_turn / commit_turn / observe
  apply_control / project / run_due_work
                |
        +-------+-------------------+
        |                           |
  CompanionStore      ReflectionModel / InitiativeScheduler
  SQLite/WAL           仅后台异步调用 / 可靠投递 Port
        |
data/xiaoxin_companion.db
```

架构约束：

- 实时路径只做本地读取、确定性策略和事务提交，不等待 ReflectionModel。
- Evidence、关系 epoch、调整、章节、主动陪伴和控制审计来自同一 SQLite 事实源。
- user Evidence 与 relationship Evidence 分离；关系重置保留前者并停用旧 epoch 派生对象。
- `voice`、`miniprogram`、`hardware`、`initiative` 和 `operator` 投影由同一 `project()` 生成。
- initiative opportunity、decision、Delivery Outcome 和明确 User Outcome 分开建模；只有 active Evidence 和当前主体/epoch 能进入调度。
- `run_due_work()` 同时处理 reflection 与 initiative，两个队列使用独立扫描预算，模型调用和设备投递不占用实时语音路径。
- 控制台通过 `apply_control()` 查看、纠正、删除和重置，不直接操作文件或拼接 SQL。
- 旧 `core.xiaoxin.memory` 和主题专用成长状态机已删除，不存在新旧双写或运行时 importer。

当前完成范围是服务端第一阶段。小程序二级记忆与隐私入口、低存在感陪伴设置、对话内自然成长表达、固件 V2 表达消费，以及关系阈值、Evidence 保留期、主动频率和硬件表达强度仍需实现或用真实数据与真机校准；不建设常驻成长阶段面板。

## 暂时保留的继承名称

以下上游名称可以暂时保留：

- Java 包名，例如 `xiaozhi.*`。
- 目录名，例如 `main/xiaozhi-server`。
- 数据库名，例如 `xiaozhi_esp32_server`。
- 上游 GitHub 链接。
- `docs/upstream-archive/` 中的原始归档文档。

只有当系统跑通、产品边界稳定后，才考虑大规模重命名。过早重命名会增加部署和排障成本。
