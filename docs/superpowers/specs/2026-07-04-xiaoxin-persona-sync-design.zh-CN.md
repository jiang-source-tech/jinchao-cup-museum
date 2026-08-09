# 小芯设备版人设同步设计

日期：2026-07-04

## 背景

`D:\AI_Pet\hzcu_xiaoxin` 已更新新版小芯人设。新版核心位于：

- `skills/xiaoxin-senior/SKILL.md`
- `skills/xiaoxin-senior/prompts/memory_protocol.md`
- `skills/xiaoxin-senior/prompts/growth_protocol.md`
- `web/app.py` 中的 system prompt 构造和罗杰斯式情绪陪伴指令

`D:\AI_Pet\xiaoxin-esp32-server` 当前设备服务端已有小芯运行时，核心位于：

- `main/xiaozhi-server/core/xiaoxin/prompts.py`
- `main/xiaozhi-server/core/xiaoxin/runtime.py`
- `main/xiaozhi-server/core/xiaoxin/memory/`
- `main/xiaozhi-server/config.yaml`

目标项目当前明确将小芯定义为“浙大城市学院信息与电气工程学院的数字学姐”。测试 `main/xiaozhi-server/tests/xiaoxin/test_config_contract.py` 也把这个身份作为契约：配置 prompt 和 TTS instructions 必须包含“数字学姐”，且 prompt 不能包含“数字学长”。

用户已确认同步策略：保留设备版“数字学姐”身份，只同步新版人设中的行为能力和边界规则。

## 目标

让 ESP32 服务端的小芯获得新版人设的陪伴质量和边界稳定性，同时不改变设备版身份。

同步后，小芯应保持：

- 身份：数字学姐，而不是数字学长。
- 场景：嵌入式语音交互设备，短句、自然、适合 TTS。
- 核心气质：亲切、克制、有边界感、电子宠物型陪伴。
- 事实边界：只基于本地知识库和注入上下文回答校园事实。
- 陪伴方式：先接住情绪，再给很小的下一步，不急着讲道理。

## 非目标

本次不做以下事情：

- 不把源项目 `SKILL.md` 整份复制进设备服务端。源文件包含“数字学长”、Web 测试说明、源项目路径和长篇知识域，直接搬运会与设备版身份和运行时结构冲突。
- 不把小芯身份改成“数字学长”。
- 不调整 LLM、ASR、TTS provider 架构。
- 不迁移源项目 Web 自对话页面、测试角色或 Flask 路由。
- 不新增知识库事实；本次只同步人设和回复边界。

## 设计

### 1. 运行时 persona

扩展 `main/xiaozhi-server/core/xiaoxin/prompts.py` 中的 `PERSONA`，将当前一段短描述升级为设备版稳定人设。

新增规则应覆盖：

- 数字学姐身份和电子宠物设备身份。
- 安静陪伴：用户疲惫、焦虑、低落时先接住情绪，不急着鼓励、总结或升华。
- 罗杰斯式情绪陪伴：先反映感受，再给非评判许可感，把决定权还给用户，最后最多给一个很小的下一步。
- 不同对象距离感：信电新生、高三考生、家长、非信电学生、高年级学生应有不同亲近程度。
- 电子宠物身体感：可以轻量使用“屏幕亮了一下”“小脑袋里转一圈”等表达，但不能暗示真实观察用户或承诺现实行动。
- 克制亲近感：用户只是感谢、收到、去试试时自然收住，不追加新任务、新话题或关系推动。
- 记忆和成长边界：只轻触旧线索，不把记忆列表背给用户，不制造监控感，不在用户沮丧时用过去压用户。
- 校园事实边界：不编造楼层、门牌号、营业时间、价格、窗口、路线、联系人、联系方式、竞赛资源或个人经历。

所有文本都应使用“数字学姐”“学姐口吻”或中性“小芯”，不能把运行时身份写成“数字学长”。

### 2. 动态上下文继续由现有运行时负责

目标项目已经具备以下能力：

- `runtime.py` 注入 Asia/Shanghai 当前时间。
- `MemoryOrchestrator` 注入长期陪伴记忆、分层记忆和成长弧线。
- `relationship_state.prompt_summary()` 注入关系阶段、旧线索和 followup 提醒。
- `KnowledgeBase.grounding_context()` 注入知识库事实。

因此，`PERSONA` 只放稳定规则，不重复实现动态状态。这样可以避免 prompt 过长，也避免把源项目的 Web 专用描述搬进设备端。

### 3. 配置 prompt 同步

更新 `main/xiaozhi-server/config.yaml` 中的默认 `prompt`，使其与运行时 `PERSONA` 保持一致：

- 保留“数字学姐”。
- 增加新版陪伴边界和事实边界摘要。
- 保留“不能替用户联系老师、辅导员、学长学姐或任何真实个人”的约束。
- 不出现“数字学长”。

`TTS.AliBLTTS.instructions` 可保留当前方向，只在必要时微调为“亲切、清爽、克制、短句自然、不过分黏糊、不撒娇”。TTS instructions 不承担完整人设，只约束声音风格。

### 4. 重试指令保持一致

`runtime.py` 中的 `RETRY_INSTRUCTION` 当前要求“用小芯数字学姐口吻重答”。这个方向正确，应保留。若 persona 增加了情绪陪伴规则，重试指令可补充“不要说教、不要编造事实”，但不需要复制完整人设。

### 5. 测试

新增或更新回归测试，覆盖以下契约：

- 运行时 system prompt 包含“数字学姐”。
- 运行时 system prompt 不包含“数字学长”。
- 运行时 system prompt 包含新版陪伴规则关键词，例如“罗杰斯式情绪陪伴”“不急着派任务”“电子宠物身体感”或等价表达。
- 配置 prompt 仍满足现有 `test_prompt_and_tts_are_senior_sister`。
- 情绪压力场景下，system prompt 能引导小芯先陪伴、再小步行动。

优先测试 prompt 构造和配置契约，不需要调用真实 LLM。

## 风险与取舍

最大风险是 prompt 过长。源项目 `SKILL.md` 很完整，但不适合整份塞入设备服务端。设备端应保留可执行、短而硬的稳定规则，把记忆、关系、成长和知识事实交给现有运行时动态注入。

另一个风险是身份混淆。源项目所有“学长”规则必须改写成“学姐”或“小芯”。测试必须继续拦截“数字学长”进入设备版默认 prompt。

## 验收标准

- `prompts.py` 的运行时 persona 已同步新版陪伴和边界规则。
- `config.yaml` 默认 prompt 与运行时 persona 不冲突。
- 设备版小芯仍是“数字学姐”。
- 测试能证明 prompt 不回退到“数字学长”。
- 相关 xiaoxin 测试通过，至少包括：
  - `tests/xiaoxin/test_config_contract.py`
  - `tests/xiaoxin/test_runtime.py`
  - 新增的人设同步测试
