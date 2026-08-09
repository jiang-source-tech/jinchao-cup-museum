# 小芯完整人格与知识运行时设计

## 摘要

目标是把 `D:\AI_Pet\hzcu_xiaoxin` 中的小芯数字学姐行为迁移到 ESP32 服务端 `main/xiaozhi-server`。

这不是只改提示词。目标行为包括小芯人格、校园知识 grounding、语义路由、硬边界处理、分层记忆、关系成长状态、回复修复和 TTS 风格对齐。ESP32 服务端继续负责 WebSocket、ASR、TTS、IoT 工具、音乐、退出处理和既有部署形态。

## 目标

- 用小芯替换当前小智式角色。
- 保留小芯边界：不编造校园事实、不假装真实在场、不提供私人联系方式、不冒充官方渠道、不承诺处理现实事务。
- 使用既有 `hzcu_xiaoxin` 知识 JSON 作为校园事实来源。
- 迁移语义路由，让闲聊、校园知识、代写文本和硬边界场景分别处理。
- 迁移 profile、companion、episodic、growth arc、relationship state 等分层记忆。
- 保留 ESP32 服务端既有语音、设备、TTS、工具和部署能力。
- 通过一个小接口接入小芯逻辑，避免把人格逻辑散落到 `ConnectionHandler`。

## 不做

- 不保留 `hzcu_xiaoxin\web\app.py` 作为 ESP32 必须调用的第二个 HTTP 服务。
- 第一阶段不迁移记忆审计 Web UI。
- 不替换 ASR、TTS provider、设备鉴权、MQTT gateway 或 manager API。
- 不默认复制旧项目里的真实用户记忆数据。

## 方案

在服务端进程内新增深模块：

```text
main/xiaozhi-server/core/xiaoxin/
```

外部入口是：

```python
result = xiaoxin_runtime.handle_turn(
    user_id=device_or_speaker_user_id,
    user_text=query,
    history=recent_dialogue,
    llm=conn.llm,
    session_id=conn.session_id,
)
```

返回结果包含是否已处理、回复、模型、路由、记忆结果、关系状态和旁路原因。

`handled=False` 表示继续走既有 ESP32 工具调用链路。`ConnectionHandler.chat()` 仍负责 TTS、工具、对话历史、中断行为和 WebSocket 生命周期。小芯运行时负责人格、路由、知识 grounding、记忆提交、边界兜底和回复修复。

## 模块布局

```text
main/xiaozhi-server/core/xiaoxin/
  runtime.py
  prompts.py
  semantic_router.py
  boundary_guard.py
  knowledge.py
  response_guard.py
  memory/
    profile_memory.py
    companion_memory.py
    episodic_memory.py
    growth_arc.py
    relationship_state.py
    memory_use_policy.py
    memory_orchestrator.py
```

实现应从旧项目中抽取纯逻辑，移除 Flask 路由和 UI 行为。

## 数据存储

运行时记忆存放在：

```text
main/xiaozhi-server/data/xiaoxin_memory/
```

校园知识存放在：

```text
main/xiaozhi-server/data/xiaoxin_knowledge/
```

用户 scope 优先使用稳定声纹身份，其次设备 ID，最后才用 session 作为临时兜底。原始用户 ID 必须规范化或哈希后再作为文件名。

## 数据流

1. ASR 或文本消息进入 `ConnectionHandler.chat(query)`。
2. 如果启用小芯运行时，连接层把 query、历史、LLM、设备 ID、speaker 和 session 传给 `XiaoxinRuntime.handle_turn`。
3. 运行时先识别明显设备动作，例如退出、音乐、IoT。
4. 记忆控制命令优先本地处理。
5. 加载关系状态并分析本轮消息。
6. 路由到硬边界、校园知识、普通闲聊或文本代写。
7. 拼装人格、知识、记忆和关系上下文。
8. 调用现有 LLM provider 的非流式适配器。
9. 检查回复是否碎片化、越界、虚构知识或路由不匹配。
10. 必要时重试或兜底。
11. 提交记忆和关系变化。
12. 交还连接层做 TTS 和对话历史保存。

## LLM 集成

回复修复更适合完整文本，因此建议增加：

```python
llm.complete_chat(messages, max_tokens=None, temperature=None) -> str
```

如果只有小芯使用，可先把适配器放在 `core/xiaoxin` 内部。后续多个模块需要时，再上移到 LLM provider 基类。

## 配置

新增配置块：

```yaml
xiaoxin_runtime:
  enabled: true
  knowledge_dir: data/xiaoxin_knowledge
  memory_dir: data/xiaoxin_memory
  max_tokens: 800
  free_chat_temperature: 0.8
  knowledge_temperature: 0.35
  boundary_temperature: 0.5
```

角色配置应改成小芯身份和基础规则。TTS 说明应从上游风格改为温暖、克制、清楚的数字学姐语气。

## 工具处理

音乐、退出、IoT、天气、搜索和 manager API 仍由现有工具链路处理。小芯运行时不执行这些工具。

如果请求既像边界问题又像设备动作，真实设备动作优先。例如“拜拜”仍走退出；“帮我联系老师”不是设备动作，应由边界逻辑处理。

## 错误处理

- 路由失败时默认普通闲聊，并启用边界 guard。
- 知识文件加载失败时明确说明没有可靠资料。
- 记忆读写失败时继续对话并记录日志。
- LLM 调用失败时使用既有系统错误回复。
- 回复修复仍失败时使用路由级兜底。
- 记忆控制成功时直接返回本地规则回复。

## 测试

优先为小芯模块补单元测试，再接连接层：

- 语义路由测试。
- 知识 grounding 测试。
- 边界 guard 测试。
- 记忆控制测试。
- 回复 guard 测试。
- fake LLM 的运行时集成测试。
- 连接层 smoke 测试。

## 验收

- 校园问题只使用已迁移知识，资料不足时诚实兜底。
- 关系和记忆问题能召回已提交的安全记忆。
- 记忆控制命令能修改存储。
- 越界请求能自然拒绝并给出用户可自行操作的建议。
- 退出类请求仍走 ESP32 原有行为。
- TTS 听感符合小芯数字学姐，而不是上游小智。
- 音乐、IoT 和工具行为仍可用。

## 实施风险

主要风险是小芯运行时与现有函数调用、流式链路产生职责冲突。设计通过特性开关和职责拆分降低风险：小芯处理普通对话，设备动作继续交给原工具路径。
