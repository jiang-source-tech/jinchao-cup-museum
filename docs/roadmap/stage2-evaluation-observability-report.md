# 阶段 2：真实评测、用户体验与在线观测报告

## 1. 验收范围

- 项目：金潮杯博物馆项目
- 阶段：阶段 2（真实评测、用户体验与在线观测）
- 验收时间：2026-08-13（Asia/Shanghai）
- 当前生产基线：`0d2b429fc5a75ef5742243a5dec0edaad843e2e9`
- 验收方式：服务端文本接口和隔离数据库评测
- 明确未执行：真机麦克风、ASR、TTS、扬声器和屏幕验收

本报告只记录已经执行或可以复核的结果。文本接口结果不等同于真机语音链路通过。

## 2. 评测集与覆盖

阶段 2 评测集版本为 2，包含原始场景和全展品覆盖矩阵：

- `197` 个场景
- `224` 轮文本对话
- `17/17` 件受管展品覆盖
- 每件展品至少覆盖规范名称、2 个审核别名、2 种事实类型、相似展品干扰、资料不足拒答和连续对话

覆盖矩阵结果：`expected_exhibit_count=17`、`passed_exhibit_count=17`、`passed=true`。

## 3. 离线质量结果

### 3.1 SQLite 规则基线

运行标识：`stage2-local-rules-3`

- 224/224 轮通过，`failed_turn_count=0`
- 规范名称、审核别名和 ASR 别名解析准确率：`1.00`
- 歧义错误绑定率：`0.00`
- 越界回答率：`0.00`
- 资料不足编造率：`0.00`
- 正确拒答率：`1.00`
- 连续会话上下文准确率：`1.00`
- Retrieval Recall@3：`1.00`
- 依据审计可复核率：`1.00`

### 3.2 Hybrid 检索路径

运行标识：`stage2-local-hybrid`。本次使用内存 Qdrant 和确定性本地 embedding，仅用于验证生产 hybrid 管线的结构、过滤、融合和审计，不代表真实 DashScope embedding 的语义质量。

- 224/224 轮通过，`failed_turn_count=0`
- 17/17 件展品覆盖通过
- Retrieval Recall@3：`1.00`
- 越界回答率：`0.00`
- 正确拒答率：`1.00`
- 连续会话上下文准确率：`1.00`
- Dense fallback：`0` 次
- hybrid 文本总时延 P50/P95：`65/98 ms`
- 检索阶段 P50/P95：`36/60.7 ms`

离线 hybrid 结果不能替代生产 Qdrant 可用性、真实 `text-embedding-v4` 调用和真实 LLM 文本验收。

## 4. 自动化检查

执行命令覆盖阶段 2 运维和评测回归测试：

```text
pytest -q tests/test_museum_stage2_operations.py tests/test_museum_conversation_eval.py
```

结果：`7 passed, 2 warnings`。

覆盖内容包括旧审计表兼容、时间窗口只读汇总、Canary 成功/失败判定、异常脱敏、规则评测、hybrid 评测路径和 CLI 参数。

## 5. Readiness、Canary 与指标

### 5.1 本地 readiness

结果：未通过。SQLite 完整性和已发布事实检查通过，但本地配置中的 Qdrant 不可达（`ResponseHandlingException`）。本地数据库只有 6 条演示事实，不是生产 17 件展品数据，因此不能把该结果当作生产 readiness。

### 5.2 本地 Canary

结果：未通过。当前本地 `config.yaml` 使用显式展品上下文、关闭演示数据 seed，Canary 运行时没有生产设备放置和 17 件展品数据库，4 个样例均得到 `missing_context`。这是本地运行环境缺失，不是通过业务失败；生产部署后必须使用生产库、生产 Qdrant 和真实配置重新执行 4 个样例。

### 5.3 离线指标汇总

对 `stage2-local-hybrid.db` 的只读汇总成功：

- 请求数：`224`
- 失败数：`0`
- 失败率：`0.0`
- Dense fallback：`0`
- 总时延 P50/P95：`65/98 ms`
- 观测 schema 缺失列：`[]`

## 6. 生产验收待办

阶段 2 代码可以提交，但阶段 2 只有在生产环境完成以下文本验收后才算生产闭环完成：

1. 核对本地、`origin/main` 和服务器三方提交及工作区状态。
2. 服务器以 fast-forward 更新并按项目规定显式传入 `.env` 启动 Compose。
3. 检查容器、日志、数据挂载和 `GET http://127.0.0.1:8003/museum/ota/` 健康入口。
4. 验证生产仍为 17 件展品、88 条公开事实和 88 个 Qdrant 点，且未挂载旧项目或阶段 3 隔离内容。
5. 在生产服务器执行 readiness、4 个 Canary、指标汇总和自由文本对话，记录回答、事实 ID、来源 ID、状态和时延。
6. 生产验收不得声明真机麦克风、ASR、TTS 或扬声器通过。
