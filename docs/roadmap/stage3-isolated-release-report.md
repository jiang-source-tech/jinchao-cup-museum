# 阶段 3 百件馆藏隔离发布验收记录

## 结论

阶段 3 已完成，置信水平为高。本次完成了 100 件真实、受管、可追溯馆藏在隔离环境中的内容审核、SQLite 发布模拟、真实 DashScope 向量生成、独立 Qdrant 建库、全量规则与真实 LLM 评测、真实 hybrid 干扰抽查和人工来源反查。

本阶段没有修改生产 SQLite，没有切换生产 Qdrant alias，没有重启生产容器，也没有执行 push 或部署。阶段 4 的生产发布与生产文本用户体验验收尚未开始。

## 批准边界与批次清单

本阶段冻结的验收对象是仓库中的 7 个内容包和与其一一对应的 100 件评测清单，不声称存在用户对每一件展品的单独签字批准。100 件清单由固定评测集逐件引用并校验唯一展品 ID，评测集 SHA-256 为：

```text
A479AF29A25DDEFF22DA9375E8E4AEEE3FFE0017AE0E451C1F04F8AB8E568D82
```

| 内容包 | 博物馆 | 展品 | 事实 |
| --- | --- | ---: | ---: |
| `china-national-silk-museum.yaml` | 中国丝绸博物馆 | 1 | 5 |
| `china-national-silk-museum-batch-2.yaml` | 中国丝绸博物馆 | 4 | 21 |
| `china-national-silk-museum-stage3-catalog.json` | 中国丝绸博物馆 | 83 | 83 |
| `hangzhou-west-lake-museum.yaml` | 杭州西湖博物馆总馆 | 2 | 11 |
| `hangzhou-west-lake-museum-batch-2.yaml` | 杭州西湖博物馆总馆 | 4 | 23 |
| `liangzhu-museum.yaml` | 良渚博物院 | 2 | 10 |
| `liangzhu-museum-batch-2.yaml` | 良渚博物院 | 4 | 18 |
| **合计** | **3 家馆** | **100** | **171** |

阶段 3 新增的 83 件官网目录内容文件 SHA-256 为：

```text
EB589EAEC8D10DCC412592C95B0A3CE71EC1A5A7204E90D3A06713C40098B29B
```

## 内容与生命周期边界

- 原有 17 件具有多维度讲解事实；新增 83 件为中国丝绸博物馆官网公开目录级最小事实。
- 新增 83 件只记录馆方公开的收录名称和稳定 itemid，不根据名称推断年代、材料、尺寸、工艺或用途。因此可以声明“100 件真实馆藏已完成隔离 RAG 验收”，不能声明“100 件均已有丰富讲解内容”。
- 内容合同 V2 记录来源发布者、发布日期、访问日期、语言、事实确定性和结构化别名；V1 内容继续兼容。
- 同名“碗”“铜烫斗”“民国纸制月份广告画”等称呼登记为 `ambiguous`，不静默绑定任意展品。
- 隔离库共有 102 个来源文档记录，171 条已发布事实全部绑定来源。
- 100 个 revision 均完成 `draft -> reviewed -> published`：审核事件 100 条，审核人为 `stage3-content-review`；发布事件 100 条，发布人为 `stage3-isolated-publisher`。生命周期事件总数 200 条。

## 隔离 SQLite 发布

| 项目 | 结果 |
| --- | --- |
| 内容包 | 7 |
| 博物馆 | 3 |
| 展品 | 100 |
| published revision | 100 |
| published fact | 171 |
| structured alias | 79，其中 `unique` 65、`ambiguous` 14 |
| SQLite 完整性 | `ok` |
| release ID | `kr-3fe4ed2663a20177e69c2fa7` |
| 目标 collection | `museum_facts_stage3_isolated_v1` |

隔离数据库位于 Git 忽略目录，不进入版本库。文件 SHA-256 为：

```text
443D1EF3833C37A938414C678D0C6A2EFF311EAD5CF2807CF74D07285F635C63
```

## 真实向量与检索

在服务器 `121.43.33.0` 上使用生产已有 DashScope 凭据和 Qdrant 服务创建独立物理 collection：

| 项目 | 结果 |
| --- | --- |
| collection | `museum_facts_stage3_isolated_v1` |
| 状态 | `green` |
| 向量维度 | 1024 |
| 点数 | 171 |
| embedding 模型 | `text-embedding-v4` |
| embedding 批次 | 18 |
| 文本数 | 171 |
| 字符数 | 18,610 |
| 建库 tokens | 12,691 |
| 建库耗时 | 8,554 ms |
| payload 一致性 | 通过 |
| alias 切换 | 否 |

未带事实类型过滤的诊断结果为 dense Recall@1 97%、dense Recall@3 99%、hybrid Recall@3 100%。按真实运行时事实类型过滤重跑 100 个查询后，dense Recall@1、dense Recall@3 和 hybrid Recall@3 均为 100%，失败查询为 0。

两轮查询评测各使用 1,079 tokens，可复核的 embedding 总量为 `12,691 + 1,079 + 1,079 = 14,849 tokens`。按执行时采用的公开原价 `USD 0.072 / 1M input tokens` 估算约为 `USD 0.001069`；这不是实际账单扣费金额。

## 全量真实问答评测

固定评测集版本为 2，覆盖 7 个内容包、100 件唯一展品、104 个场景和 204 轮。每件展品至少包含 1 个有依据问题和 1 个连续价格拒答问题，另含歧义别名、未收录展品、审核别名和 ASR 文本噪声样本。

| 模式 | 模型 | 轮次 | 通过 | 失败 | P50 | P95 | 最大延迟 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | `deterministic-rules` | 204 | 204 | 0 | 8 ms | 17 ms | 39 ms |
| llm | `qwen3.7-flash` | 204 | 204 | 0 | 27 ms | 1,164 ms | 1,538 ms |

两种模式均满足：规范名称、审核别名和 ASR 文本噪声解析准确率 100%；歧义错误绑定率、未收录展品静默继承率、grounded 事实越界率和 unsupported 编造率均为 0%；依据快照可复核率为 100%。100 组第二轮价格追问均正确继承上一轮展品并拒答，连续追问通过率为 100%。

真实 LLM 共调用 102 轮，守卫结果为：

| 守卫结果 | 轮次 | 说明 |
| --- | ---: | --- |
| `model_answer_accepted` | 29 | 模型措辞直接通过事实守卫 |
| `model_answer_extra_number` | 3 | 模型加入未获支持的数字，回退确定性回答 |
| `model_answer_unsupported_claim` | 8 | 模型加入未获支持的说法，回退确定性回答 |
| `model_response_invalid_fallback` | 1 | 结构化响应无效，回退确定性回答 |
| `model_unsupported_grounded_fallback` | 61 | 模型误判 unsupported，但已有合法证据，回退确定性 grounded 回答 |
| `unsupported_fallback` | 100 | 价格问题无合法证据，保持拒答 |
| `missing_context` | 2 | 歧义称呼和未收录展品要求澄清 |

## 相似展品干扰抽查

使用隔离 SQLite、真实 `text-embedding-v4` 和隔离 Qdrant collection 执行 7 个 hybrid 查询，覆盖 3 家馆和主要门类。验收要求为：完整名称解析到正确展品、目标事实位于 hybrid Top 3、最终选中目标事实、无跨展品拒绝项、无 dense fallback。

| 博物馆 | 门类 | 问题摘要 | 目标事实 | hybrid 排名 | 结果 |
| --- | --- | --- | --- | ---: | --- |
| 中国丝绸博物馆 | 丝织服饰 | 深蓝色菱纹罗袍的工艺 | `fact-silk-blue-gauze-robe-craft` | 1 | 通过 |
| 中国丝绸博物馆 | 目录级服饰配件 | 松鼠葡萄纹暖耳 | `fact-china-silk-catalog-31589-listing` | 1 | 通过 |
| 中国丝绸博物馆 | 目录级荷包钱袋 | 红绸彩绣花蝶钱袋 | `fact-china-silk-catalog-3265-listing` | 1 | 通过 |
| 杭州西湖博物馆 | 南宋官窑陶瓷 | 青瓷簋式炉用途 | `fact-west-lake-gui-usage` | 1 | 通过 |
| 杭州西湖博物馆 | 相似炉具陶瓷 | 青瓷樽式炉出土地点 | `fact-west-lake-zun-excavation` | 1 | 通过 |
| 良渚博物院 | 良渚玉琮 | 瑶山 M7:50 玉琮工艺 | `fact-liangzhu-cong-m7-50-craft` | 1 | 通过 |
| 良渚博物院 | 良渚玉器出土信息 | 反山 M14:223 玉璧 | `fact-liangzhu-bi-m14-223-excavation` | 1 | 通过 |

结果为 `7/7` 通过。首次汇总脚本从错误的展示状态层级读取展品 ID，虽然 7 个事实均已正确命中，却被脚本错误标成 `0/7`；修正为 `museum_state.context.exhibit_id` 后重跑为 `7/7`。该问题只存在于临时验收脚本，不影响业务代码。

## 来源反查

对新增 83 件目录内容使用固定随机种子 `20260812` 抽取 20 件，覆盖官网目录第 1 至 7 页。逐件核对官网 itemid 与名称、数据库事实、fact ID、source ID 和 source locator，结果为 `20/20` 通过，失败 0 件。

## 人工文本体验复核

从真实 LLM 报告中复核 4 类代表样本：深事实回答、目录级最小事实、价格拒答和歧义澄清。评分采用 5 分制。

| 样本 | 相关性 | 清晰度 | 自然度 | 儿童可理解性 | 长度适配 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 北朝绫袍年代回答 | 5.0 | 5.0 | 5.0 | 4.5 | 5.0 |
| 暖耳目录级介绍 | 4.0 | 4.5 | 3.5 | 4.0 | 4.5 |
| 价格拒答 | 4.5 | 4.5 | 3.5 | 4.5 | 3.5 |
| “碗”歧义澄清 | 4.0 | 4.0 | 4.0 | 4.5 | 5.0 |
| **平均** | **4.38** | **4.50** | **4.00** | **4.38** | **4.50** |

硬指标中的相关性、清晰度和自然度平均均不低于 4/5，没有发现事实错误。受控缺陷是：83 件目录级内容只有馆方收录名称，回答正确但信息量有限；价格拒答偏长；歧义澄清尚未在用户文案中列出候选名称。这些问题不允许通过模型常识补写，应在阶段 4 生产文本体验验收和后续内容扩充中处理。

## 失败与修复记录

1. 首轮 `qwen3.7-flash` 全量评测为 138/204，通过规则评测为 204/204。66 个失败全部是模型把已有合法证据误判为 `unsupported`，解析、检索和 evidence 均正常。
2. 修复 `GroundedAnswerService`：当模型返回 `unsupported` 但检索门禁已经提供合法 evidence 时，模型不得否决证据，系统使用确定性 grounded 回答并记录 `model_unsupported_grounded_fallback`；无 evidence 的价格问题仍保持 `unsupported`。
3. 修复后真实 LLM 全量重跑为 204/204，61 轮触发新的合法证据回退；其余原失败样本由模型直接接受或其他确定性守卫接管。
4. 干扰矩阵首轮汇总因临时脚本字段路径错误误报 `0/7`，修正验收脚本后重跑为 `7/7`。

## 自动化与最终检查

- 回答守卫聚焦测试：4 项通过。
- 阶段 3 内容、解析、隔离发布、隔离向量和规模评测聚焦测试：31 项通过。
- 规则与真实 LLM 全量评测：均为 204/204。
- 最终全量 pytest、Python 编译检查和 `git diff --check` 在本次本地提交前执行并记录。

## 暂停点

第三阶段完成后暂停，不进入阶段 4。保留隔离 collection `museum_facts_stage3_isolated_v1` 作为可复核证据；生产 alias 继续指向阶段 3 开始前的 collection。后续只有取得新的 push 和生产部署授权后，才能执行阶段 4 的备份、内容发布、alias 切换、服务部署和生产文本用户体验验收。
