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
- `main/xiaozhi-server/tests/test_museum_runtime.py`：14 passed。
- 仓库示例数据库审计：`integrity=ok`，manifest 可生成，未命中旧身份词。
- `git diff --check`：通过。

## 尚未满足的退出条件

1. 尚未连接 `121.43.33.0` 完成生产 SQLite 只读审计和备份校验。
2. 生产当前 18 件展品、94 条事实与仓库 17 件、88 条事实的差异，尚未决定“战国水晶杯”正式纳入或撤回。
3. 尚未核对生产 Qdrant alias、collection、点数、1024 维度、`text-embedding-v4` 和内容哈希。
4. 尚未取得生产容器、日志、挂载路径和健康入口的验收证据。

## 结论

阶段 1 仍为 `in-progress`。本轮完成了防止未来 seed 漂移的代码门禁，但没有把未经生产证据支持的状态宣称为完成。下一步必须在获准的服务器会话中执行只读审计与备份，然后按内容生命周期命令处理“战国水晶杯”，再重建并核对索引。
