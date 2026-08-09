# Xiaoxin MQTT Overview 服务端最终审查修复报告

日期：2026-07-11
分支：`codex/xiaoxin-mqtt-overview-server`
范围：仅服务端；未修改固件、小程序或部署配置。真机验收状态保持 pending。

## 结论

整分支审查的 8 组 findings 均已闭环。新增行为均先由确定性失败测试复现，再以最小生产改动修复。保留既有 owner serialization、location revision/CAS、MQTT session/PUBACK 隔离、ACK deadline 和诊断脱敏语义。

## Findings 闭环

1. Manager API 配置合并
   - `get_config_from_api_async()` 以本地配置为基底递归合并远端配置；远端显式键覆盖本地，未提供键继续保留。
   - 覆盖 `read_config_from_api=true` 场景，验证本地 `xiaoxin_control.overview_mqtt.db`、`ip_hmac_secret`、`trusted_proxy_cidrs` 保留，远端 `enabled` 与 `identity_db` 显式覆盖。

2. Disabled 启动隔离
   - `overview_mqtt.enabled=false` 时不解析/创建 Overview DB 路径、不打开 SQLite、不构造 IP/weather provider。
   - 新增 projection-only `DisabledOverviewSyncService`；课程/待办 HTTP projection 继续可用。
   - refresh/clear/observe/drain/location mutation 返回稳定 `overview_mqtt_disabled`，不触发磁盘或网络。
   - 用“父路径是普通文件”的不可写 DB 路径证明 disabled runtime 仍可创建。

3. 完整 schema 迁移
   - `device_weather_locations` 逐列补 `country_code`、全部 `automatic_*`、`location_revision`，并为 automatic 旧行回填当前 HMAC/省市/国家/定位时间。
   - `daily_city_weather` 补 `timezone_id` 和 `quarantined`。
   - `device_overview_snapshots` 补 `quarantined`。
   - 从真实早期三表 schema 启动后，location/weather/snapshot 读写均成功。

4. malformed queue quarantine
   - weather retry `cache_key` 和 pending snapshot `payload_json` 逐行解析。
   - malformed weather 行设置 `quarantined=1`、`next_attempt_at=NULL`、`overview_retry_row_malformed`。
   - malformed snapshot 行设置 `quarantined=1`、`next_attempt_at=NULL`、`overview_payload_invalid`。
   - 扫描不再让 SQL limit 被坏行耗尽：即使 5 条坏行排在 limit=1 的正常行之前，正常行仍返回；重启后坏行不重复阻塞。
   - 有效 weather/snapshot upsert 重置 quarantine。

5. same-IP cache 与并发去重
   - 先计算 HMAC，再进入 device lock；在锁内读取 automatic/current HMAC。
   - 相同 HMAC 直接返回，provider 调用数为 0。
   - 不同 IP 在同一 device lock 内 await provider，并发同 IP 只定位一次。
   - 定位写回后仍执行 owner 复核和 location revision/CAS 回滚。

6. 首次天气失败持久化
   - 普通 refresh 首次 provider 失败写入 city/date/provider retry 行：attempts=1、+600 秒、稳定错误 `overview_weather_fetch_failed`。
   - city/date/provider 共享串行锁使同城并发失败只调用/记录一次。
   - runtime due retry 继续执行 1800/7200/NULL 后退序列，service 不再双记录 runtime retry。
   - 重启整合测试证明 600 秒 retry 存活，到期由 runtime 成功刷新并清除 retry state。

7. Feature gate 一致行为
   - disabled manual `overview-mqtt-sync` 返回 HTTP 503 和 `overview_mqtt_disabled`，不生成 snapshot。
   - disabled weather PATCH 返回相同 503 稳定码且不访问 store/provider。
   - CRUD trigger 与 OTA observation 调用 null service 时无磁盘/外网副作用；业务 handler 不因 Overview disabled 抛错。

8. 共享网络安全逻辑
   - 新增 `core/xiaoxin/network_observation.py`，统一 trusted CIDR 解析、fail-closed 代理链提取和真实 global-unicast 判断。
   - OTA/control handler 均委托共享 helper，移除两份漂移实现。
   - 删除生产代码对 `203.0.113.10` 的硬编码例外；正向测试使用 `8.8.8.8`/真实 global IPv6，文档地址只作为拒绝用例。

## TDD 证据摘要

- 配置 merge + disabled isolation：2 个测试先失败，修复后 2 passed。
- 早期 schema migration + quarantine：2 个测试先失败，修复后 2 passed。
- same-IP + concurrent IP + first weather failure：3 个测试先失败，修复后 3 passed。
- manual disabled gate + public-IP exception removal：2 个测试先失败，修复后通过。
- weather PATCH disabled：先返回旧 `weather location unavailable` 而失败，修复后稳定 gate 通过。
- 多条 malformed 行抢占 limit：先返回空列表而失败，移除坏行抢占后通过。

## 验证

- Focused core affected：`96 passed in 14.14s`。
- 序列化回归 isolated：`1 passed in 5.30s`。
- 完整 Xiaoxin suite（最终 fresh JUnit）：`568 tests, 0 failures, 0 errors, 1 skipped, 74.449s`。
- Requirements workbench：`7 passed in 0.44s`。
- `python -m compileall -q config core/xiaoxin core/api tests/xiaoxin`：exit 0。
- 关键模块 import smoke：`imports: PASS`。
- `git diff --check`：exit 0（仅 Git 的 LF→CRLF 工作树提示，无 whitespace error）。

## 自审与剩余顾虑

- 配置默认仍为 `overview_mqtt.enabled=false`；未写入任何真实 secret。
- quarantine 诊断只暴露稳定错误码，不返回坏 payload/cache key 内容。
- 未改 MQTT owner/session/PUBACK/ACK deadline 代码路径；完整 suite 覆盖无回归。
- 真机 retained receipt、离线重连、真实城市天气、课程/待办刷新和解绑清空仍按既有验收台账保持 pending。
- Windows Loguru 文件轮转会在并发 pytest 进程下产生文件锁；验证时已改为单一后台 pytest 进程。该环境噪声未造成测试 failure/error，不属于本次 8 项功能范围。
