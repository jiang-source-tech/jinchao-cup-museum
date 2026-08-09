# Xiaoxin 独立身份与记忆控制台设计

日期：2026-07-04

## 背景

当前 `main/xiaozhi-server` 已经有两条与记忆相关的路径：

- 上游通用 Memory provider，由 `selected_module.Memory` 选择，当前本地配置为 `nomem`。
- Xiaoxin 专用分层记忆，由 `xiaoxin_runtime.enabled: true` 启用，存储在 `data/xiaoxin_memory`。

Xiaoxin 专用记忆已经包含 `profile`、`episodic`、`companion`、`growth_arc`、`relationship_state` 等层。但当前对话作用域主要由 `speaker or device_id` 推导，未知说话人、多人共用设备、同名说话人、多设备账号归属等场景都可能造成记忆污染。

用户确认采用以下方向：

- 身份模型采用“账号 + 设备 + 声纹说话人”混合模式。
- `8003/xiaoxin/control/` 建设 Xiaoxin 自己的综合控制台。
- `8003` 账号系统独立于 `8002 manager-api`。
- 第一阶段采用 SQLite 管身份与隔离关系，现有记忆文件继续保留。

## 目标

建立 Xiaoxin 独立身份中心和记忆归属模型，让每轮对话都解析到稳定、唯一、不可读的 `memory_subject_id`，再把这个 ID 作为现有分层记忆的 scope。

第一阶段完成后，应满足：

- `8003/xiaoxin/control/` 有独立注册、登录和 session。
- 账号可以绑定设备。
- 设备下可以管理说话人。
- 每轮对话由 `device_id + speaker + binding` 解析到 `memory_subject_id`。
- 未知说话人不会落入全局记忆桶。
- 控制台可以查看、删除、清空当前主体的记忆。
- 现有记忆文件层不用一次性重写。

## 非目标

第一阶段不做以下事情：

- 不复用 `8002 manager-api` 的用户体系。
- 不把全部记忆内容迁入 SQLite。
- 不实现复杂 RBAC；本地账号第一阶段均视为 Xiaoxin 控制台管理员。
- 不自动强迁旧 scope 记忆文件。
- 不实现多人家庭权限细分。
- 不做花哨 UI 优先于身份模型。

## 核心判断

这个项目的关键不是“加一个登录页面”，而是建立一个正确的身份 seam：

```text
Connection / Control Console
        ↓
XiaoxinIdentityResolver
        ↓
memory_subject_id
        ↓
MemoryOrchestrator / existing memory stores
```

记忆层只认 `memory_subject_id`，不再关心账号、设备、speaker、未知说话人。身份复杂性集中在 `XiaoxinIdentityResolver` 和 SQLite identity store 中。

## 存储设计

新增 SQLite 文件：

```text
main/xiaozhi-server/data/xiaoxin_control.db
```

SQLite 不作为独立服务运行。它是服务器进程打开读写的本地文件，随 `python app.py` 一起使用。

### users

```text
id                  TEXT PRIMARY KEY
username            TEXT UNIQUE NOT NULL
password_hash       TEXT NOT NULL
display_name        TEXT NOT NULL
created_at          TEXT NOT NULL
last_login_at       TEXT
```

`id` 使用 `usr_<ulid>` 或 UUID。用户名不能作为内部关联主键。

### sessions

```text
id                  TEXT PRIMARY KEY
user_id             TEXT NOT NULL
token_hash          TEXT UNIQUE NOT NULL
expires_at          TEXT NOT NULL
created_at          TEXT NOT NULL
last_seen_at        TEXT
```

浏览器只保存 session token cookie；数据库只保存 token hash。

### devices

```text
id                  TEXT PRIMARY KEY
owner_user_id       TEXT
device_id           TEXT UNIQUE NOT NULL
display_name        TEXT NOT NULL
bind_status         TEXT NOT NULL
created_at          TEXT NOT NULL
last_seen_at        TEXT
```

`device_id` 是设备上报标识，`id` 是控制台内部设备 ID。绑定后 `owner_user_id` 指向当前账号。

### speaker_profiles

```text
id                  TEXT PRIMARY KEY
owner_user_id       TEXT
device_id           TEXT NOT NULL
speaker_key         TEXT NOT NULL
display_name        TEXT NOT NULL
status              TEXT NOT NULL
created_at          TEXT NOT NULL
last_seen_at        TEXT
UNIQUE(owner_user_id, device_id, speaker_key)
```

`speaker_key` 不使用显示名。优先使用声纹模块返回的稳定 speaker id；如果当前只有 speaker 文本，则使用：

```text
sha256(owner_user_id + device_id + normalized_speaker_name)
```

两个用户同名、两台设备同名说话人都不能共享同一个 speaker profile。

### memory_subjects

```text
id                          TEXT PRIMARY KEY
owner_user_id               TEXT
device_id                   TEXT NOT NULL
speaker_profile_id          TEXT
kind                        TEXT NOT NULL
display_name                TEXT NOT NULL
created_at                  TEXT NOT NULL
merged_into_subject_id      TEXT
```

`id` 使用 `ms_<ulid>` 或 UUID。它是唯一允许进入记忆文件 scope 的主体 ID。

`kind` 取值：

```text
user_speaker
device_unknown
device_fallback
```

### subject_aliases

```text
from_subject_id     TEXT PRIMARY KEY
to_subject_id       TEXT NOT NULL
reason              TEXT NOT NULL
created_at          TEXT NOT NULL
```

第一阶段合并 unknown 记忆时只建立 alias，不立即物理合并记忆文件。读取时 resolver 把 alias 解析到最终 subject。后续可以增加后台合并工具。

## 身份解析

新增模块：

```text
core/xiaoxin/identity/
```

核心接口：

```python
resolve_turn_subject(device_id: str, speaker: str | None, session_id: str) -> TurnIdentity
```

返回：

```text
memory_subject_id
owner_user_id
device_id
speaker_profile_id
subject_kind
is_authenticated_device
confidence
```

解析规则：

### 已绑定设备 + 已确认 speaker

```text
device_id 存在且 owner_user_id 非空
speaker 可识别
speaker_profile.status = confirmed
=> kind = user_speaker
=> memory_subject_id = ms_<stable id>
```

记忆写入个人主体。

### 已绑定设备 + 未知 speaker

```text
device_id 存在且 owner_user_id 非空
speaker 为空、None、未知说话人或未确认
=> kind = device_unknown
=> memory_subject_id = 当前账号当前设备的 unknown subject
```

未知主体必须绑定账号和设备，不能全局复用。

### 未绑定设备

```text
device_id 未绑定账号
=> kind = device_fallback
=> memory_subject_id = 当前设备 fallback subject
```

这类记忆标记为临时。控制台绑定设备后，用户可以选择导入、合并、清空或保留。

### subject alias

如果解析出的 subject 存在 alias：

```text
from_subject_id -> to_subject_id
```

resolver 返回最终 `to_subject_id`。如果 alias 链异常或形成环，resolver 截断并返回不可持久化 fallback，记录错误。

## Runtime 接入

当前逻辑类似：

```python
scope = normalize_user_scope(speaker or user_id, fallback=session_id)
```

改为：

```python
identity = identity_resolver.resolve_turn_subject(device_id, speaker, session_id)
scope = identity.memory_subject_id
```

然后继续调用现有：

```text
MemoryOrchestrator.prepare_context(...)
MemoryOrchestrator.commit_turn(...)
```

记忆文件继续使用现有格式，只是 scope 来源变成 `memory_subject_id`。

## 控制台信息架构

`/xiaoxin/control/` 第一阶段包含六个区：

### 登录 / 注册

未登录访问控制台时显示认证页。

首次启动如果数据库中没有用户，允许创建第一个账号。后续注册由配置控制：

```yaml
xiaoxin_control:
  auth:
    allow_registration: true
```

登录成功后设置 `xiaoxin_session` cookie。

### 总览

显示：

- 在线设备数。
- 可唤醒设备数。
- 最近说话人。
- 最近记忆写入。
- 最近投递状态。
- 身份解析异常。

身份解析异常包括：

- 设备未绑定。
- unknown subject 累积记忆。
- 同名 speaker 出现在多个设备。
- subject alias 尚未物理合并。

### 设备

管理：

- 已绑定设备。
- 待绑定设备。
- 设备显示名。
- 在线、可唤醒、离线状态。
- 最近连接时间。

现有 `/api/xiaoxin/devices` 升级为只返回当前登录用户可见设备。未绑定设备进入待绑定列表。

### 说话人

管理：

- confirmed speaker。
- unknown speaker。
- 修改显示名。
- 归档误识别。
- 合并 unknown 到已有主体。
- 新建家庭成员主体。

### 记忆

按主体查看：

- profile。
- companion。
- episodic。
- growth_arc。
- relationship_state。

第一阶段支持：

- 查看摘要。
- 删除单条或按关键词删除。
- 清空当前主体。
- 导出 JSON。
- 合并 unknown 主体。

不提供任意字段编辑，避免破坏内部结构。

### 投递 / 唤醒

保留现有能力：

- 发送提醒。
- 唤醒设备。
- 查看 delivery timeline。
- 查看失败原因。

所有投递 API 必须限制到当前登录用户绑定的设备。

## API 设计

新增或调整 API：

```text
POST /api/xiaoxin/auth/register
POST /api/xiaoxin/auth/login
POST /api/xiaoxin/auth/logout
GET  /api/xiaoxin/auth/me

GET  /api/xiaoxin/devices
POST /api/xiaoxin/devices/{device_id}/bind
PATCH /api/xiaoxin/devices/{device_id}

GET  /api/xiaoxin/speakers
PATCH /api/xiaoxin/speakers/{speaker_id}
POST /api/xiaoxin/speakers/{speaker_id}/confirm
POST /api/xiaoxin/speakers/{speaker_id}/archive

GET  /api/xiaoxin/memory-subjects
GET  /api/xiaoxin/memory-subjects/{subject_id}/memory
DELETE /api/xiaoxin/memory-subjects/{subject_id}/memory
POST /api/xiaoxin/memory-subjects/{subject_id}/forget
POST /api/xiaoxin/memory-subjects/{subject_id}/merge
```

控制台 API 默认要求 session。健康检查、静态页面壳和设备内部上报接口可以例外。

## Secret 与登录的关系

`xiaoxin_control.secret` 保留为安装级保护，不再作为日常登录机制。

建议语义：

- 如果 `secret` 为空，只使用账号/session。
- 如果 `secret` 非空，远程访问控制台页面或 API 需要同时满足安装级 secret 和账号 session。
- 本机访问可以打开页面壳，方便输入 secret 和登录。

## 错误处理

### SQLite 不可用

控制台 API 返回 `503`。对话运行时不能落到全局记忆；应使用不可持久化 session fallback，或使用按 `device_id` 隔离的临时 fallback，并在日志中标记 identity degraded。

原则：宁可不记，也不能错记到别人名下。

### 设备未绑定

使用 `device_fallback`，控制台总览标红提醒。

### speaker 为空或未知

使用 `device_unknown`，不写入 confirmed user subject。

### session 过期

控制台 API 返回 `401`，前端跳登录页。

### alias 链异常

resolver 截断，记录错误，不写入可疑 subject。

### 记忆文件损坏

沿用现有 degraded 行为：备份损坏文件或降级为空记忆，但日志必须包含 `memory_subject_id`。

## 迁移策略

第一阶段不自动迁移旧记忆文件。

旧 scope 可能来自：

- speaker 文本。
- device_id。
- session_id。
- 历史 fallback。

自动迁移容易把旧脏数据写入新主体。保守策略：

1. 新系统启用后，新记忆全部写入 `memory_subject_id` scope。
2. 旧记忆文件保留原样。
3. 控制台提供 Legacy Memory 区。
4. 用户可以手动选择导入、只查看、删除或忽略。

只有高置信场景可以提示自动导入，例如：

```text
旧 scope == 当前 device_id
且该设备只绑定一个账号
且没有多个 speaker
```

即便如此，默认仍应让用户确认。

## 测试策略

### 身份解析

- 已绑定设备 + confirmed speaker 解析到 `user_speaker`。
- 已绑定设备 + unknown speaker 解析到 `device_unknown`。
- 未绑定设备解析到 `device_fallback`。
- 同名 speaker 在不同设备下生成不同 profile。
- 同名 speaker 在不同账号下生成不同 profile。
- subject alias 能解析到最终主体。
- alias 环被拒绝或截断。

### 记忆隔离

- 用户 A 和用户 B 同名 speaker 不共享记忆。
- 同一用户不同 speaker 不共享 private memory。
- unknown speaker 不会落到 confirmed speaker。
- 未绑定设备不写入任何用户主体。
- 清空当前主体只清当前 `memory_subject_id` 对应文件。
- 关键词删除只作用于当前主体。

### 控制台鉴权

- 未登录访问控制台 API 返回 `401`。
- 登录后只能看到自己的设备。
- 用户 A 不能查看用户 B 的 `memory_subject`。
- session 过期后跳登录。

### 设备绑定

- 未绑定设备出现在待绑定列表。
- 绑定后归属当前账号。
- 绑定后的投递 API 只能投给自己的设备。

### 降级

- SQLite 不可写时不串写到全局 scope。
- resolver 失败时不持久化错误主体记忆。
- 记忆文件损坏时仍按现有 degraded 行为恢复。

## 实施顺序

1. 新增 SQLite identity store 和 schema 初始化。
2. 新增认证模块：注册、登录、登出、session cookie。
3. 新增 `XiaoxinIdentityResolver`。
4. 在 `ConnectionHandler` / `XiaoxinRuntime` 接入 resolver，把 scope 切到 `memory_subject_id`。
5. 升级控制台 API 鉴权和设备过滤。
6. 增加登录、总览、设备、说话人页面。
7. 增加记忆查看、删除、清空、合并页面。
8. 增加 Legacy Memory 区。
9. 补齐测试和文档。

## 验收标准

- 未登录不能访问控制台 API。
- 首次启动可以创建第一个 Xiaoxin 控制台账号。
- 登录后可以绑定设备。
- 已绑定设备的 confirmed speaker 写入 `user_speaker` subject。
- 已绑定设备的 unknown speaker 写入 `device_unknown` subject。
- 未绑定设备写入 `device_fallback`，不写入用户主体。
- 不同账号、不同设备、同名 speaker 的记忆互不污染。
- 控制台能查看并清空当前主体的分层记忆。
- 投递和唤醒 API 只允许操作当前账号绑定设备。
- 旧记忆不会被自动强迁。
