# 小芯租户文档清理设计

**日期：** 2026-07-10
**状态：** 已确认，待实施
**范围：** 文档治理，不修改运行代码、数据库结构或现有配置类

## 目标

删除已经失效的 tenant-aware MQTT 设计和实施计划，并修正仍在使用的部署、控制台、需求和 Overview 文档，使后续任务不会把“先完成租户化”误判为当前功能的前置条件。

## 当前有效事实

当前产品按单组织部署设计和验收：

- 小程序用户通过微信 `openid` 建立登录身份；
- 服务端通过 `owner_user_id + device_id` 记录设备归属；
- 硬件使用设备级 MQTT 凭证连接 Broker；
- Broker ACL 按 `device_id` 限制设备只能访问自己的主题；
- 小程序写操作必须校验当前登录用户是否拥有目标设备；
- MQTT 主题不包含租户命名空间；
- OTA 和业务 Payload 不把 `tenant_id` 作为设备协议必填字段。

当前主题合同固定为：

```text
device/{device_id}/status
device/{device_id}/notification
device/{device_id}/overview
```

服务端 ACL：

```text
topic read device/+/status
topic write device/+/notification
topic write device/+/overview
```

设备 ACL：

```text
topic write device/{device_id}/status
topic read device/{device_id}/notification
topic read device/{device_id}/overview
```

## 删除范围

直接删除以下过时文档，不保留废弃副本：

```text
docs/superpowers/specs/2026-07-08-doorbell-mqtt-tenant-aware-design.zh-CN.md
docs/superpowers/plans/2026-07-08-doorbell-mqtt-tenant-aware-implementation.md
```

删除理由：

- 两份文档把 `tenant/{tenant_id}/device/{device_id}/...` 写成目标主题；
- 它们要求固件、OTA、ACL 和授权流程先完成租户化；
- 这些要求已经被后续单组织、无租户主题决策取代；
- 保留在可搜索的 specs/plans 目录会持续误导自动化任务和人工执行者。

Git 历史已经保留原始内容，因此无需在当前文档树中继续保存失效副本。

## 修正范围

### 部署文档

修正 `docs/getting-started/deployment.md`：

- 删除首租户部署检查项；
- 删除 `{tenant_id}:{device_id}` 必须作为 client ID/username 的要求；
- 把 status、notification 和 Overview 检查项统一为 `device/{device_id}/...`；
- 把学生唤醒授权改为“已登录账号拥有已绑定设备”；
- 保留设备级凭证、Mosquitto 认证文件和 ACL 检查。

### 控制台开发文档

修正 `docs/development/xiaoxin-control-console.md`：

- 将 wake 和 status 主题改为无租户主题；
- 增加 Overview retained 主题的职责说明；
- 删除 `tenant_mismatch` 作为当前错误分类；
- 将授权条件改为 session、设备绑定和 owner 校验；
- 明确 WebSocket 只承担语音、音频、TTS 和 ACK，MQTT 承担状态、wake 和 Overview。

### 需求账本

修正 `docs/requirements/requirements.yaml` 中与当前决策冲突的条目：

- 已完成事实必须描述实际存在的无租户主题；
- 删除“运行 topic 尚未向 tenant-scoped 合约收敛”的硬伤描述；
- OTA 字段列表不再声称 `tenant_id` 是设备协议要求；
- wake 授权不再要求 tenant 匹配；
- 诊断状态不再把 `tenant_mismatch` 列为当前门铃路径错误；
- 历史决策明确标记为已被后续无租户主题决策取代，或直接改写为当前有效决策；
- 多租户只能作为未来条件性演进，不得列入当前验收、阻塞项或实施前置任务。

### Overview 文档

修正 `docs/superpowers/specs/2026-07-10-xiaoxin-mqtt-overview-sync-design.zh-CN.md` 中残留的“不同用户和租户数据隔离”，改为“不同用户和设备数据隔离”。

检查四份 2026-07-10 Overview 实施计划，确保没有 tenant-scoped 主题、租户 Payload 校验或租户迁移步骤。现有代码文件名和类名可以作为准确路径保留。

## 历史代码与配置的处理边界

本次不重命名或删除以下现有实现：

- `core/xiaoxin/tenant_config.py`；
- `TenantConfig`；
- 已有数据库中的 `tenant_id` 字段；
- 当前配置文件中的 `xiaoxin_control.tenant`；
- 设备凭证内部使用的部署命名空间。

这些属于现有服务端实现和历史数据兼容问题。文档必须把它们描述为内部实现细节，不能据此推导出以下要求：

- MQTT 主题必须增加租户前缀；
- 固件必须解析 `tenant_id`；
- Overview Payload 必须携带 `tenant_id`；
- 当前功能必须先完成 tenant-aware 改造；
- 当前验收必须覆盖跨租户隔离。

如果未来确实出现多个独立组织共用同一部署和 Broker，应重新提出需求、完成设计评审，再决定是否迁移数据模型和主题。未来可能性不构成当前任务。

## 防止再次误导的规则

后续涉及 MQTT、设备绑定、Overview、wake 或真机验收的任务必须优先使用当前有效文档，不得从已删除的 2026-07-08 方案恢复租户前置要求。

文档检查规则：

1. 当前 MQTT 主题示例不得出现 `tenant/{tenant_id}/device/...`。
2. 当前 OTA 和 Overview Payload 示例不得包含 `tenant_id`。
3. 当前授权说明必须使用登录 session、owner 和设备绑定关系。
4. 当前 ACL 必须按设备主题隔离。
5. “租户”只能出现在历史实现说明或未来条件性演进说明中。
6. 未来多租户不得进入当前完成标准、验收矩阵或实施顺序。

## 验证标准

实施完成后执行以下核对：

- 两份 2026-07-08 tenant-aware 文档不存在；
- 活跃文档不存在 tenant-scoped MQTT 主题；
- deployment 和控制台文档的主题与实际代码一致；
- requirements.yaml 不再把租户化列为待完成硬伤；
- Overview 规格和计划只要求用户/设备隔离；
- Markdown code fence 成对；
- YAML 可以被解析；
- `git diff --check` 无错误。

## 非目标

- 不修改 Python、C++、小程序代码；
- 不迁移 SQLite 数据；
- 不重命名现有服务端模块；
- 不更改已经部署的 MQTT 用户名或密码；
- 不在本次任务中设计多租户架构。
