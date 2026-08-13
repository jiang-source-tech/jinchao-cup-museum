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

## 6. 生产部署与文本验收（2026-08-13）

本节记录通过 SSH 在唯一生产服务器 `121.43.33.0` 上执行的可复核结果。验收范围是服务端文本链路，不包含真机麦克风、ASR、TTS、扬声器或屏幕。

### 6.1 部署与运行状态

- 本地 `HEAD`、`origin/main` 和服务器 `HEAD` 均为 `2df8ef8`（`完成博物馆阶段二评测与在线观测`），服务器工作区干净。
- `jinchao-museum-server` 和 `jinchao-museum-qdrant` 均为 `healthy`。
- `GET http://127.0.0.1:8003/museum/ota/` 返回 HTTP 200，正文包含 `ws://121.43.33.0:8000/museum/v1/`。
- 生产数据挂载为 `/opt/jinchao-cup-museum-data`，未挂载旧项目数据目录。

### 6.2 内容发布与向量索引门禁

- `check_museum_readiness.py`：`ready=true`，SQLite integrity=`ok`，published facts=`88`，Qdrant expected/actual=`88/88`，无 missing、unexpected、duplicate、invalid 或 mismatch；release=`kr-74a68a2beac54315a4de5687`。
- `verify_museum_knowledge_release.py --qdrant-url http://qdrant:6333`：通过；manifest 使用 `text-embedding-v4`、1024 维，88/88 payload 一致。
- Qdrant alias `museum_facts_v1` 指向 `museum_facts_v1__build_0dbcc22f661f411a94e5b02ce6b0d6bb`，该生产 collection 为 88 点。`museum_facts_v1__build_96d2511ee66543aea006e12f6f83ae09` 为 94 点历史构建，`museum_facts_stage3_isolated_v1` 为 171 点阶段 3 隔离索引，二者均未承载生产 alias 流量。
- 数据库统计：`exhibit` 共 18 条 active 记录，其中 17 条具有 published revision；published `exhibit_fact` 为 88 条，另有 6 条 withdrawn fact；`source_document` 为 21 条。

### 6.3 真实文本自由对话

通过 `scripts/museum_text_chat.py --require-llm --json` 发送 UTF-8 中文（使用 Base64 传输，避免 SSH 客户端编码改写），结果如下：

| 场景 | 结果 | 关键证据 |
| --- | --- | --- |
| `玉三叉形器是什么材质？` | 通过 | grounded；答案为“南瓜黄色的玉器”；fact=`fact-liangzhu-trident-material`；source=`source-liangzhu-jade-trident-2019393530`；LLM=`qwen3.7-flash`；guard=`model_answer_accepted` |
| `玉钺组合现在市场价多少钱？` | 通过 | unsupported；受控拒答；guard=`unsupported_fallback`；未调用 LLM |
| `南宋官窑青瓷八卦熏炉盖有多高？` | 通过 | grounded；答案为“4.4 厘米”；fact=`fact-west-lake-bagua-dimensions`；source=`source-west-lake-bagua-lid-853`；LLM=`qwen3.7-flash` |
| `你能介绍一下杭州博物馆的展品吗？` | 受控澄清 | missing_context；提示先说出展品名称；未调用 LLM |

同一文本会话中先问“玉三叉形器是什么材质？”，再问“它是什么年代的？”：第一轮 grounded 且正确继承展品；第二轮识别为 `inherited_session`，但因当前馆方资料没有该展品的年代事实而受控拒答。这证明会话指代链路有效，缺口是内容覆盖而非检索或会话崩溃。

### 6.4 生产日志与结论

文本验收后最近 20 分钟容器日志未发现 `traceback`、`error`、`exception` 或 `failed`。因此，阶段 2 的生产部署、Hybrid RAG 检索、真实 DashScope embedding、Qdrant 发布门禁、真实 LLM 调用、拒答和审计链路通过服务端文本验收。当前生产范围仍是 17 件展品、88 条公开事实；阶段 3 的 100 件扩展数据仍处于隔离索引，尚未切换到生产。

本次结论不等同于真机语音链路通过；真机验收需另行使用 `museum-firmwire` 当前固件执行。
