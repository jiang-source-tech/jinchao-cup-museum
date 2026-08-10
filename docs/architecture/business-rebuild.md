# 博物馆业务层重建设计

## 状态

设计日期：2026 年 8 月 9 日。本文描述目标架构，尚未表示代码已经实现。产品范围、用户体验和优先级以 [`../product/PRD.md`](../product/PRD.md) 为上位依据。现有调用链以 [`current-runtime-audit.md`](current-runtime-audit.md) 为准，具体实施顺序以 [`../roadmap/business-rebuild-execution-plan.md`](../roadmap/business-rebuild-execution-plan.md) 为准。

## 1. 重建判断

旧业务层围绕学生身份、声纹、长期陪伴记忆、课程、待办和主动投递构建。博物馆场景围绕展品、来源、审核版本、设备点位、游客会话和参观路线构建。两者的核心实体、不变量和失败语义不同，因此不能通过替换名称或增加提示词完成迁移。

重建采用“保留平台，替换业务”的方式：

| 层级 | 处理 |
| --- | --- |
| WebSocket、OTA、音频编解码、可靠 TTS | 保留 |
| ASR、LLM、TTS、VAD 提供商适配 | 保留 |
| 设备在线状态和固件遥测 | 保留并收窄接口 |
| 学生身份、声纹、课程、待办、陪伴记忆 | 不进入新业务运行时 |
| 博物馆内容、问答、路线、现场回顾、运营分析 | 从零建立 |

## 2. 最重要的 seam

设备连接层不再直接依赖旧陪伴运行时。新建一个小接口：

```python
class ConversationRuntime(Protocol):
    def handle_turn(self, request: TurnRequest) -> TurnOutcome: ...
```

`TurnRequest` 只包含调用业务所需的稳定输入：

```text
request_id
session_id
device_id
user_text
history
occurred_at
```

`TurnOutcome` 返回连接层真正需要的结果：

```text
spoken_text
emotion
display_state
grounding_summary
journey_state
audit_record
```

连接层只负责把 ASR 文本交给接口，并把结果交给现有 TTS、屏幕和日志通道。展品检索、路线推进、事实守卫和运营记录全部隐藏在 `MuseumRuntime` 实现内部。

这个模块应当是深模块：连接层只学习一个接口，却获得完整博物馆对话行为。

## 3. 目标代码结构

```text
main/xiaozhi-server/
├── core/
│   ├── conversation_runtime.py
│   ├── museum/
│   │   ├── runtime.py
│   │   ├── contracts.py
│   │   ├── catalog.py
│   │   ├── context.py
│   │   ├── retrieval.py
│   │   ├── answer_policy.py
│   │   ├── answer_guard.py
│   │   ├── journey.py
│   │   ├── telemetry.py
│   │   └── store.py
│   └── api/
│       ├── museum_admin_handler.py
│       └── static/museum_control.html
├── data/
│   └── museum/
│       ├── museum.db
│       └── seed/
└── tests/
    └── museum/
```

文件名是设计目标，可以在实现时根据现有代码约定微调，但模块职责不得重新混回单个巨大控制器。

## 4. MuseumRuntime 外部接口

`MuseumRuntime.handle_turn()` 完成以下全部行为：

1. 验证请求与会话。
2. 解析显式控制指令，例如切换模式、选择展品和推进路线。
3. 取得权威的当前展品。
4. 分类本轮意图。
5. 建立依据快照。
6. 生成回答草稿。
7. 执行事实、长度、问题预算和表达守卫。
8. 必要时修复回答或输出知识兜底。
9. 推进路线状态。
10. 生成固件显示状态。
11. 写入交互审计和未命中问题。
12. 返回单一 `TurnOutcome`。

调用方不能分别调用路由、检索和守卫，否则业务规则会泄漏到连接层和接口处理器中。

## 5. 内部模块

### MuseumCatalog

负责展馆、展区、展品、展品事实、资料来源和内容版本。它必须保证只有发布版本能够进入游客回答。

主要接口：

```text
publish_revision(revision_id)
get_published_exhibit(exhibit_id)
build_evidence_snapshot(exhibit_id, question)
```

### MuseumContext

负责确定当前展品。权威顺序为：

1. 游客本轮明确选择；
2. 当前路线站点；
3. 会话中已确认的当前展品；
4. 设备点位默认展品。

模型推测不得成为当前展品来源。上下文冲突时必须澄清或回到设备点位，而不是静默猜测。

### MuseumRetrieval

第一版使用 SQLite 和 FTS5，根据展品 ID、别名、事实类别和问题关键词检索。20 至 30 件展品不需要外部向量数据库。检索结果必须返回事实 ID、来源 ID和内容版本，而不是只返回拼接文本。

### MuseumAnswerPolicy

把依据快照、游客模式和路线状态编译成模型输入。它规定：

- 默认 2 至 4 句；
- 一轮最多一个观察任务或下一站建议；
- 亲子模式降低语言难度，不修改事实；
- 深度模式可以增加工艺和历史关联，但仍受依据快照限制；
- 禁止冒充历史人物或馆方；
- 禁止把推测写成馆方结论。

### MuseumAnswerGuard

回答守卫是确定性业务模块，不应只依赖第二次 LLM 判断。第一版至少检查：

- 回答为空或残缺；
- 回答长度和问题数量超限；
- 具体年代、人物、地点、材质、工艺或用途未出现在依据快照；
- 资料不足时仍使用确定语气补充事实；
- 亲子模式出现不适合语音理解的长句；
- 模型声称已经切换或已经推进，但状态提交失败。

守卫失败时最多修复一次；再次失败直接使用知识兜底，不无限重试。

### MuseumJourney

路线使用确定性状态机，不让模型决定任意跳转。

```text
not_started -> active -> completed
                    -> abandoned
```

模型可以提出下一站建议，但只有显式游客操作或明确语音指令才能提交路线进度。

### MuseumTelemetry

记录可复核的交互结果：

```text
request_id
session_id
device_id
exhibit_id
visitor_mode
question
fact_ids
source_ids
content_version
grounding_status
guard_result
spoken_text
asr_ms
retrieval_ms
llm_ms
tts_first_packet_ms
created_at
```

默认不记录声纹、姓名和长期人物画像。原始音频是否保存必须另行决定，比赛版本默认不保存。

## 6. 管理后台

新后台使用 `/api/museum/*`，不继续扩展旧小程序接口。

第一版只提供五组能力：

| 页面 | 核心操作 |
| --- | --- |
| 展品内容 | 新建展品、编辑事实、绑定来源 |
| 审核发布 | 查看差异、发布或撤回内容版本 |
| 设备点位 | 设置设备展区和默认展品 |
| 路线编辑 | 配置站点、任务和顺序 |
| 运营记录 | 查看高频问题、未命中、守卫失败和延迟 |

后台不承担模型供应商全量配置、声纹、课程、长期记忆或主动提醒管理。

## 7. 馆方二维码边界

馆方既有二维码可以继续提供展品图文、音视频和延伸阅读，但它与博物馆语音讲解运行时保持独立。讲解助手不生成故事卡、数字纪念册、收藏二维码或参观后网页，也不把扫码访问作为现场对话的完成条件。

产品闭环必须在展品现场完成：确定当前展品、进行有依据问答、给出一个观察任务并推进参观路线。游客结束会话时，设备只显示简短现场回顾并清除上一小组的可见状态。

## 8. 配置与运行模式

建议增加明确配置：

```yaml
business_runtime:
  type: museum

museum:
  database_path: data/museum/museum.db
  admin_enabled: true
  default_visitor_mode: general
  answer_max_chars: 180
  question_budget: 1
  save_raw_audio: false
```

比赛部署不得同时启用旧学生陪伴运行时和新博物馆运行时，以免同一问题被两套业务逻辑竞争处理。

## 9. 迁移顺序

### 阶段 A：建立 seam

- 定义 `TurnRequest`、`TurnOutcome` 和 `ConversationRuntime`；
- 让连接层通过接口调用现有运行时；
- 保持现有语音链路行为不变。

### 阶段 B：最小博物馆纵向链路

- 建立 SQLite 数据库和 6 至 8 件展品种子数据；
- 实现当前展品、依据快照、回答策略和知识兜底；
- 在文字输入和真机输出链路中验收。

### 阶段 C：切换运行时

- 配置 `business_runtime.type=museum`；
- 比赛部署关闭旧身份、声纹、课程、待办、陪伴记忆和主动投递；
- 保留旧代码但从启动路径断开。

### 阶段 D：后台、路线和现场回顾

- 建立馆方后台；
- 增加三类路线；
- 增加结束会话和设备现场回顾；
- 对接固件的新博物馆状态合同。

### 阶段 E：删除遗留业务代码

博物馆主链路、后台、固件和部署稳定后，按调用关系保留传输兼容层，删除不再使用的旧业务代码和旧测试。兼容路径不得重新承载学生陪伴语义。

## 10. 测试与验收接口

自动化测试只穿过深模块接口，优先保护：

1. 已发布事实能够生成有依据回答；
2. 未发布或不存在的事实触发知识兜底；
3. 游客模式变化不改变事实 ID；
4. 路线进度只有在显式提交后变化；
5. 守卫失败不会把未验证回答交给 TTS。

具体实现任务仍遵循项目每次变更最多新增 0 至 3 个聚焦测试的规则。完整知识评测集属于验收数据，不等同于单元测试数量。

## 11. 删除标准

一个旧模块只有同时满足以下条件才能删除：

- 新运行时不再导入它；
- HTTP 和 WebSocket 路径不再调用它；
- 固件不再依赖其消息结构；
- 配置、Docker、脚本和测试不再引用它；
- 真机主路径通过；
- Git 历史能够承担回溯，不再需要把旧文档留在有效文档中心。
