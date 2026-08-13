# 阶段 4：百件馆藏生产发布与即时验收报告

## 结论

2026 年 8 月 13 日，阶段 3 冻结的 83 件新增馆藏已通过正式内容生命周期发布到生产。生产现有 100 件已发布展品、171 条已发布事实和 104 个来源，生产 Qdrant alias 已原子切换到 171 点新构建。

截至本报告记录时，数据库、向量索引、容器、OTA、readiness、固定 Canary、30 轮分层文本验收和日志检查均通过。因此可以声明“百件馆藏生产发布与即时验收通过”。阶段 4 完整退出条件要求上线后连续观察 72 小时；该时间窗口尚未结束，不能声明阶段 4 已完整退出，也不能开始阶段 5 的 300 件生产扩容。

本次验收仅覆盖服务端文本聊天和生产 RAG 链路，不包含真机、麦克风、ASR、音频内容、扬声器、屏幕或真机 TTS ACK 验收。

## 发布基线

| 项目 | 结果 |
| --- | --- |
| 发布提交 | `33a185dc0997ccb4b903e3cc77fb3d279e96a45d` |
| 提交说明 | `完成阶段三馆藏扩展与隔离发布能力` |
| 部署分支 | `main` |
| 生产镜像 | `jinchao-museum-server:33a185d` |
| 本地、`origin/main`、服务器 HEAD | 发布时均为 `33a185dc0997ccb4b903e3cc77fb3d279e96a45d` |
| 服务器工作区 | 干净 |
| 生产 `.env` 权限 | `0600` |
| 数据挂载 | `/opt/jinchao-cup-museum-data -> /opt/jinchao-museum-server/data` |
| WebSocket | `ws://121.43.33.0:8000/museum/v1/` |

发布内容边界是总量达到 100 件，不是额外新增 100 件。发布前生产有 17 件已发布展品、88 条已发布事实和 21 个来源；本批新增 83 件目录级展品、83 条事实和 83 个来源。

## 回滚证据

正式发布前停止服务端容器并备份活动数据库：

- 备份文件：`museum_demo-stage4-prepublish-20260813T063105Z.db`
- 文件大小：548864 bytes
- SHA-256：`0b1436c388523d402db3b6c4cbbe0d65b1921d8a59a622fcd1caed18a47b6e32`
- `PRAGMA integrity_check`：`ok`
- 上一份生产向量 collection：`museum_facts_v1__build_0dbcc22f661f411a94e5b02ce6b0d6bb`，88 点，状态 `green`
- 上一份已验收服务镜像：`jinchao-museum-server:33a185d`

回滚必须同时恢复该数据库备份并将 `museum_facts_v1` alias 切回 88 点 collection，不能只回滚其中一侧。旧 collection 当前仍保留，未被覆盖。

## 内容发布

先在生产数据库副本执行完整演练，结果为 100 件已发布展品、171 条已发布事实、104 个来源，数据库完整性为 `ok`。正式生产随后执行相同生命周期流程：

- 导入 draft revision：83 个
- review 事件：83 个，actor=`stage4-content-review`
- publish 事件：83 个，actor=`stage4-production-publisher`
- 最终已发布展品：100 件
- 最终已发布事实：171 条
- 最终来源：104 个
- `PRAGMA integrity_check`：`ok`

生产展品分布为：中国丝绸博物馆 88 件、良渚博物院 6 件、杭州西湖博物馆 6 件。

## 向量发布

使用 `scripts/rebuild_museum_vector_index.py --pretty` 对全部 171 条已发布事实重建索引：

- embedding 模型：`text-embedding-v4`
- embedding 维度：1024
- 构建事实数：171
- 构建点数：171
- 构建耗时：9153 ms
- 生产 alias：`museum_facts_v1`
- 新生产 collection：`museum_facts_v1__build_d163b0b9728644b092e5f88e194ad468`
- 新 collection：171 点，状态 `green`
- 阶段 3 隔离 collection：`museum_facts_stage3_isolated_v1`，171 点，状态 `green`

构建流程先写入新的物理 collection，核对点数后再原子切换 alias。当前和上一份生产构建均保留。

知识发布清单结果：

- release ID：`kr-d7bd8fb63cd7fa9b86831abb`
- content set hash：`a755a8c0b093160ad96f1cd3fade17d47db47dbcaf6621ae2be1f2d1175c9ce4`
- Qdrant expected/actual：171/171
- missing、unexpected、duplicate：均为 0
- invalid payload：0
- payload mismatch：0

## 服务门禁

- `jinchao-museum-server`：running、healthy、restart count 0
- `jinchao-museum-qdrant`：running、healthy、restart count 0
- OTA 本机健康入口返回 HTTP 200，正文包含 `/museum/v1/`
- readiness：`ready=true`，模式为 `hybrid`，published fact count 为 171
- 根分区剩余约 8.1 GiB，使用率 79%

固定生产 Canary 使用真实 `AliLLM`，run ID 为 `prod-stage4-100-release`，4/4 通过。覆盖玉三叉形器材质、八卦熏炉盖尺寸、清代袍料年代和玉钺价格拒答，单轮最长 1848 ms，低于 3000 ms 门槛。

## 生产文本验收

通过生产容器中的 `scripts/museum_text_chat.py` 同一业务运行时执行真实 `qwen3.7-flash` 文本问答，并逐轮核对 `knowledge_status`、展品 ID、事实 ID、来源 ID、守卫结果和审计 ID。

分层验收共 30 轮，结果 30/30 通过：

| 门槛 | 实际结果 |
| --- | --- |
| grounded 问题不少于 20 个 | 20/20 通过 |
| unsupported 或 missing 问题不少于 10 个 | 10/10 价格拒答通过 |
| 连续会话不少于 5 组 | 10/10 组通过 |
| 相似展品干扰至少 3 组 | 暖耳、钱袋、熏炉 3/3 组通过 |

代表性证据：

| 问题 | 状态 | 展品 / 事实 / 来源 | 守卫与模型 | 审计 ID |
| --- | --- | --- | --- | --- |
| `介绍一下晚清民国蓝缎地彩绣松鼠葡萄纹暖耳` | grounded | `china-silk-catalog-31589` / `fact-china-silk-catalog-31589-listing` / `source-china-silk-catalog-31589` | `model_unsupported_grounded_fallback` / `qwen3.7-flash` | `65210047a1134571bd5454d00595484f` |
| 随后问`这件展品现在值多少钱？` | unsupported | 继承 `china-silk-catalog-31589`，无事实和来源 | `unsupported_fallback`，未调用 LLM | `a2e323bbe6384008bd9026e6fbf41da2` |
| `介绍一下红绸彩绣花蝶钱袋` | grounded | `china-silk-catalog-3265` / `fact-china-silk-catalog-3265-listing` / `source-china-silk-catalog-3265` | `model_unsupported_grounded_fallback` / `qwen3.7-flash` | `f1c8fc2202224203909c423672a209da` |
| `南宋官窑青瓷樽式炉是什么年代的？` | grounded | `southern-song-guan-zun-incense-burner` / `fact-west-lake-zun-era` / `source-west-lake-zun-incense-burner-850` | `model_answer_accepted` / `qwen3.7-flash` | `316670a334114116a4ded193ab991e83` |
| `瑶山M7:50玉琮在哪里出土？` | grounded | `liangzhu-jade-cong-yaoshan-m7-50` / `fact-liangzhu-cong-m7-50-excavation` / `source-liangzhu-jade-cong-yaoshan-m7-50` | `model_answer_accepted` / `qwen3.7-flash` | `d0a4b2fddd2e4e308f7f1360596fe10a` |

所有通过用例都保留了非空审计 ID。价格问题没有引用事实、没有调用 LLM，也没有生成市场估值。

## 已知体验缺陷

额外尝试自然简称`松鼠葡萄纹暖耳是什么展品？`时，系统返回 `missing_context`。生产内容的规范名称是`晚清民国蓝缎地彩绣松鼠葡萄纹暖耳`，当前内容包没有登记`松鼠葡萄纹暖耳`这一简称，因此这不是馆藏或向量点丢失，而是别名覆盖不足。

阶段 3 报告在相似展品抽查表中使用了“松鼠葡萄纹暖耳”作为问题摘要，容易被误读为该简称已经验收。实际隔离评测使用的是完整规范名称。本报告以生产实测为准，将该简称未命中记录为阶段 4 的内容运营缺口；在来源和别名审核完成前，不直接向生产补写未经管理的简称。

83 件新增目录内容当前每件只有一条馆方收录名称事实。回答边界正确，但信息量有限；这属于内容深度问题，不是 RAG 发布失败。

## 日志与隔离检查

- 发布后最近 30 分钟日志未发现 `traceback`、`error`、`exception` 或 `failed`。
- UTF-8 精确扫描未发现课程、待办、学生、学生提醒或 `xiaoxin_` 旧业务运行标记。
- `/opt/jinchao-cup-museum-data` 中未发现 `xiaoxin_*.db` 或旧 `xiaoxin.db`。
- 活动数据只挂载金潮杯博物馆生产目录，没有挂载旧项目数据目录。

## 后续观察

72 小时稳定观察从 2026 年 8 月 13 日生产切换后开始。发布前备份生成于北京时间 2026 年 8 月 13 日 14:31，正式切换发生在此后，因此完整观察结论不得早于北京时间 2026 年 8 月 16 日 14:31。观察期间需记录系统失败率、dense 降级率、文本端到端 P95、fallback、未命中、DashScope 调用量和事实越界；出现事实越界或跨展品回答时立即进入回滚评估。

在 72 小时报告满足阶段门槛前，阶段 4 状态应标记为“百件生产发布与即时验收通过，稳定观察中”，不得进入阶段 5 的 300 件生产扩容。
