# 阶段 1 基线收敛实施记录

日期：2026 年 8 月 13 日
项目：金潮杯博物馆项目

## 已完成的仓库改动

- `main/xiaozhi-server/docker-compose.yml` 为 `museum-server` 固定注入 `MUSEUM_ENV=production`。
- `main/xiaozhi-server/core/business_runtime_factory.py` 在生产环境拒绝 `seed_demo_content=true`，避免服务启动静默新增演示展品。
- `main/xiaozhi-server/scripts/audit_museum_baseline.py` 提供只读基线审计：SQLite `integrity_check`、表计数、发布事实 manifest、来源/事实可追溯性和旧身份词扫描。
- `main/xiaozhi-server/tests/test_museum_runtime.py` 增加生产 seed 拒绝回归用例。

## 本地验证

- `MUSEUM_ENV=production` 下启用 demo seed 会明确失败。
- 阶段 1 聚焦测试：24 passed。
- 仓库示例数据库审计：`integrity=ok`，manifest 可生成，未命中旧身份词。
- `git diff --check`：通过。

## 生产实施与证据

- 生产提交：`bc440fa`，服务器 `main` 与 GitHub `origin/main` 一致，工作区干净。
- 部署镜像：`jinchao-museum-server:bc440fa`；容器状态 `healthy`，重启次数为 0。
- 数据挂载：`/opt/jinchao-cup-museum-data -> /opt/jinchao-museum-server/data`。
- 部署前备份：`museum_demo-stage1-20260813T023634Z.db`，大小 462848 字节，SHA-256 为 `9c5bd982bd9c01819aa6d64e7de7b00cc95f3a1c5450ad92c41f44c863fcc6e6`，完整性为 `ok`。
- “战国水晶杯”确认是 seed 生成的演示 revision，审核标识为 `competition-demo-review`，来源备注明确限定为演示事实核对；已通过 `withdraw` 生命周期撤回，没有直接删除数据。
- 最终 SQLite：完整性 `ok`，17 个已发布展品、88 条已发布事实；所有已发布事实均有来源，17 个已发布 revision 的审核和发布时间字段完整。
- 最终 Qdrant alias：`museum_facts_v1 -> museum_facts_v1__build_0dbcc22f661f411a94e5b02ce6b0d6bb`，88 点、1024 维、Cosine、状态 green。
- 知识发布：`release_id=kr-74a68a2beac54315a4de5687`，数据库与索引 88/88 一致，无缺失、额外、重复或 payload 不匹配。
- 健康入口：OTA 返回 `/museum/v1/`；liveness 为 true；readiness 为 true。
- 生产文本验收：身份问题返回“金潮杯博物馆讲解助手”；“玉三叉形器是什么材质”命中审核事实和来源；已撤回的“战国水晶杯”问题返回资料不足，不再使用演示事实。
- 本次只完成文本聊天验收，不代表麦克风、ASR、TTS 或扬声器链路验收。

## 结论

阶段 1 已完成。仓库受管内容、生产 SQLite 和生产 Qdrant 已统一为 17 件展品、88 条事实；生产自动 seed 漂移被硬门禁阻断，旧“小芯”用户身份已从生产输出移除，内容、来源、生命周期、索引和文本业务验收均有可复核证据。
