# Task 5 Report: Overview Projection And Sync Service

## Status

完成。实现了 `OverviewSyncService`、HTTP Overview projection 抽取和兼容包装，并保持 Task 6-9 的 runtime loop、CRUD trigger、天气 API/heartbeat endpoint、diagnostics 不在本任务范围内。

## TDD Evidence

### RED 1: service 缺失

- 命令：`python -m pytest tests/xiaoxin/test_overview_service.py -q`
- 结果：exit 1，collection 按预期失败：`ModuleNotFoundError: No module named 'core.xiaoxin.overview.service'`。
- 失败原因是功能模块缺失，不是测试拼写、fixture 或环境错误。

### GREEN 1: focused service

- 初次实现后：`12 passed`。
- 自审期间新增并验证两个独立 RED→GREEN：publisher 抛异常进入 pending/backoff；未配置 IP HMAC 时禁止自动 IP 持久化。

### RED 2: handler 未委托 projection service

- 命令：`python -m pytest tests/xiaoxin/test_control_handler.py -q -k overview_handler_wrappers_delegate`
- 结果：`1 failed, 105 deselected`。
- 失败表明 `_curriculum_overview` 仍执行 handler 内建逻辑，没有调用注入的 `runtime.overview_service`。

### GREEN 2: final focused integration

- 命令：`python -m pytest tests/xiaoxin/test_overview_service.py tests/xiaoxin/test_control_handler.py -q`
- 结果：`121 passed in 25.49s`。

## Covered Boundaries

- 绑定设备生成完整 v1 wire envelope：`type/version/device_id/bound/revision/generated_at/weather/course/todo`。
- MQTT payload 只包含硬件卡片数据；不包含 notification history、`openid`、`user_id`、学号或嵌套课程/待办身份记录。
- 无位置使用规范空态；天气 provider 失败不阻断课程和待办，并且不下发供应商错误。
- 同业务内容复用原 payload/revision，且不重复 publish。
- 解绑发布更高 revision 的 `bound=false` 空 retained snapshot，旧 owner 的课程、待办和天气文本被清除。
- device owner 严格决定课程/待办来源；`refresh_user_devices` 只处理该 owner 的 bound devices。
- 文本按保守 UI 上限截断：省市 32、summary/title 48、detail 64 字符；最终 UTF-8 compact JSON 强制不超过 2048 bytes。
- `build_student_overview` 复用 curriculum/course/todo 语义但不写 snapshot、不增加 revision、不发布 MQTT。
- HTTP handler 保留 latest notification history；MQTT projection 不读取或携带 notification history。
- `mid -> (device_id, revision)` 精确映射；PUBACK 只标记对应 revision published，过时 ACK 无法覆盖新 snapshot。
- publish 返回 `None` 或抛异常时按 `1/2/5/15/30` 秒退避；新业务内容覆盖旧 pending；`drain_pending` 只投递 due snapshots。
- 自动 IP observation 在未配置 HMAC key 时明确禁用，不使用硬编码或普通 hash。

## Files

- Created: `main/xiaozhi-server/core/xiaoxin/overview/service.py`
- Created: `main/xiaozhi-server/tests/xiaoxin/test_overview_service.py`
- Modified: `main/xiaozhi-server/core/api/xiaoxin_control_handler.py`
- Modified: `main/xiaozhi-server/tests/xiaoxin/test_control_handler.py`

## Verification

- Focused service + handler: `121 passed in 25.49s`。
- Related overview store/provider, doorbell client, identity store, import smoke: `85 passed in 8.40s`。
- `python -m compileall -q ...`: exit 0。
- Direct import smoke printed `OverviewSyncService XiaoxinControlHandler`: exit 0。
- `git diff --check`: no whitespace errors；仅显示仓库既有 Windows CRLF conversion warnings。

## Self-review

- store 仍是 revision、content hash、pending overwrite 和 wire envelope 的唯一事实源；service 没有复制 revision 算法。
- handler compatibility service 只有 projection dependencies，不注册 publisher，也不会让 GET 产生 pending/revision 副作用。
- handler 只抽取 curriculum/student overview construction；旧 CRUD、WebSocket 手动 Overview、notification history 和 demo helper 保持兼容。
- publisher exception 不向课程/待办业务链路外抛；snapshot 保持 pending 并安排下一次尝试。
- `.superpowers/sdd/progress.md` 是任务开始前已有修改，本提交不包含该文件。

## Concerns / Follow-ups

- 设计文档没有给出逐字段固件字符上限；本任务采用 32/48/64 的保守上限并额外强制 2 KiB。若固件 Task 4 最终确定更小的逐字段上限，应统一常量。
- runtime construction、connect-triggered drain、daily weather retry、CRUD/bind/unbind triggers、weather location endpoints/heartbeat、diagnostics/manual resync 均按计划留给 Task 6-9。

---

## Review Follow-up: Owner Race, Early PUBACK, And In-flight Retry

### Review Status

审查指出的 1 个 Critical 和 2 个 Important 均已按确定性 RED→GREEN 修复；未实现 Task 6 runtime loop。

### Critical: stale owner projection

RED 时序：

1. owner A 的 `refresh_device` 已构建 A 课程/待办，并阻塞在 weather provider await。
2. identity store 将同一 device 从 A unbind 后绑定到 owner B。
3. 启动 B refresh，再释放 A weather await。
4. 原实现允许 A 最后持久化并覆盖 B snapshot，且没有 stale/discarded 结果。

GREEN：

- `refresh_device` 与 `clear_unbound_device` 共用 device-scoped async serialization。
- lock registry 使用引用计数；持有者、等待者和取消路径都会清理，最后一个用户退出后删除 lock，避免永久按 device 增长。
- bound projection 在持久化前重新读取 current device；owner、bind status 或 device existence 与起始状态不一致时返回 `discarded=true`，不调用 store、不发布、不增加 revision。
- clear 在持久化前同样重读 bind state；若设备已经重新绑定则丢弃 stale clear。
- 确定性测试结果：owner race focused case RED 后 GREEN，最终 snapshot 只含 B 数据。

### Important: PUBACK before `publish_overview` returns

RED 时序：

- fake publisher 分别在当前线程和独立线程中，于 `publish_overview` 返回 mid 之前触发 ACK。
- 原实现尚未登记 `mid -> (device, revision)`，ACK 被丢弃，snapshot 永久保持 pending。

GREEN：

- 每次 publish 建立临时 early-ACK window；未知 ACK 只在正在执行的 publish window 中记录。
- mapping 登记与 early ACK reconciliation 共用线程锁；返回 mid 后若窗口已见该 mid，立即精确 `mark_published`。
- 每个 window 最多记录 64 个 mid，并在 publish 返回或抛异常后立即删除；窗口外未知 mid 不缓存，不存在无界 orphan ACK 集合。
- timed-out mapping 在重试前进入最多 256 项的 retired-mid quarantine；陈旧 ACK 先被消费，不能借 mid 重用误标新 attempt published。
- 同步 early ACK、线程 early ACK、stale reused-mid 三个时序均通过。

### Important: successful publish immediately duplicated by drain

RED：

- 第一次 publish 成功取得 mid 但不 ACK。
- 原 snapshot 的 `next_attempt_at` 仍为 `NULL`；立即 `drain_pending` 会重复 publish。

GREEN：

- 固定并命名 `PUBLISH_ACK_TIMEOUT_SECONDS = 10`。
- store 新增 `mark_publish_in_flight`，成功取得 mid 后持久化 future ACK deadline，但不增加 refusal attempt counter。
- deadline 前 snapshot 不出现在 due pending；deadline 到达后才允许 QoS 1 duplicate retry。
- ACK 使用现有 `mark_published` 清除 deadline。
- publish 返回 `None` 或抛异常仍使用原 `1/2/5/15/30` refusal backoff；成功 in-flight scheduling 不改变该序列。

### Follow-up Verification

- `python -m pytest tests/xiaoxin/test_overview_service.py -q`: `18 passed in 4.93s`。
- `python -m pytest tests/xiaoxin/test_overview_service.py tests/xiaoxin/test_control_handler.py -q`: `125 passed in 27.78s`。
- `python -m pytest tests/xiaoxin/test_overview_store.py tests/xiaoxin/test_doorbell_client.py -q`: `36 passed in 2.77s`。
- compileall/import: exit 0；import 输出 ACK timeout `10` 与 service/store 类名。
- `git diff --check`: 无 whitespace error，仅有仓库既有 CRLF conversion warning。

### Follow-up Self-review / Concerns

- per-device lock 只覆盖 Task 5 的 refresh/clear critical section，没有新增 runtime loop 或 CRUD trigger。
- identity store 的外部 bind/unbind 不受 service lock 控制，因此 persist 前 owner/bind 重验是必要的第二道防线；不能只依赖 serialization。
- MQTT mid 本身不携带 publish generation。对于 timeout 后重用同一 mid，当前策略优先避免 false-published：retired mid 的首个 ACK 被视为陈旧并丢弃；若它实际属于新 attempt，则 snapshot 保持 pending 并在 ACK timeout 后安全重试。
- early ACK 和 retired mid 都有明确上限与清理路径；不会永久保存任意未知 mid。

---

## Second Review Follow-up: Safe Pending Drain

### Critical Finding

第二次复审确认 `drain_pending()` 绕过了 device serialization 和 identity validation。虽然 refresh/clear 已受锁保护，但 runtime/restart drain 仍可直接把持久化的旧 owner snapshot 交给 `_attempt_publish`。

### RED Evidence

并发时序：

1. store 中存在 owner A 的 due revision 1 snapshot。
2. identity 已切换到 owner B。
3. B refresh 持有 device lock，并阻塞在 weather provider await。
4. 并发启动 `drain_pending()`。
5. 原实现不等待 lock，立即发布 A revision 1；释放 B 后才发布 B。

确定性 RED 断言捕获到 publisher 历史中仍含 `A 的旧课程` 和 `A 的旧待办`。

重启式时序：

1. 无任何并发 refresh，仅保留 A due snapshot。
2. identity 当前 owner 已是 B。
3. 调用 `drain_pending()`。
4. 原实现直接发布 A revision 1，没有生成更高 revision 的 B snapshot。

两个 focused case 初次运行结果：`2 failed, 18 deselected`，失败均为旧 A payload 实际被发布，而非测试环境错误。

### GREEN Implementation

- store 新增最小只读接口 `get_snapshot(device_id)`，返回该设备当前唯一 snapshot，不改变 revision/pending 状态。
- drain 仍先取得 due 列表，但每个 list item 随后必须进入与 refresh/clear 相同的 `_serialize_device(device_id)`。
- 锁内重新读取 store current snapshot，并验证：
  - current snapshot 存在；
  - revision 与原 list item 一致；
  - state 仍为 pending；
  - `next_attempt_at` 仍已到期。
- bound payload 仅在 snapshot `owner_user_id` 与 identity 当前 bound owner 完全一致时允许 `_attempt_publish`。
- unbound payload 仅在 identity 当前确实没有 bound owner 且 snapshot owner 为空时允许发布。
- identity mismatch 时不发布 list item；锁内调用不重复获取锁的 `_refresh_device_locked` 或 `_clear_unbound_device_locked`，生成/coalesce 当前 B 或 `bound=false` snapshot。
- 若 current identity 的业务卡片与旧 snapshot 恰好内容相同，store 只更新 owner、不升 revision；drain 随后重新读取 coalesced pending snapshot 并尝试发布，避免永久跳过或每个 tick 反复扫描旧 owner 行。
- 若并发 refresh 已生成更高 revision，drain 锁内发现 list revision stale 后直接跳过；该新 snapshot 已由 refresh 自己发布/安排 ACK deadline，不产生 duplicate。

### GREEN Evidence

- 两个 drain focused cases：`2 passed, 18 deselected`。
- `python -m pytest tests/xiaoxin/test_overview_service.py -q`: `20 passed in 5.95s`。
- `python -m pytest tests/xiaoxin/test_control_handler.py -q`: `107 passed in 22.96s`。
- `python -m pytest tests/xiaoxin/test_overview_store.py tests/xiaoxin/test_doorbell_client.py -q`: `36 passed in 2.94s`。
- compileall/import: exit 0；import 确认 `OverviewSyncService` 与 `XiaoxinOverviewStore.get_snapshot`。
- `git diff --check`: 无 whitespace error，仅有既有 CRLF conversion warning。

### Second Follow-up Self-review

- drain、refresh、clear 现在共享同一 device critical section；内部 coalesce helper 不重复获取 lock，因此没有自锁死路径。
- list query 只是候选扫描，所有安全决定都基于锁内重新读取的 current snapshot 和 current identity。
- early ACK reconciliation、10 秒 ACK deadline、retired-mid quarantine 和 refusal backoff 均继续由 `_attempt_publish` 统一处理；drain 没有复制这些状态机。
- 本次未增加 runtime loop、CRUD trigger、weather endpoint 或 diagnostics。
