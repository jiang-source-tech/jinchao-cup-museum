# 小芯拟人化 AI 互动合规改造实施计划

日期：2026-08-07
时区：Asia/Shanghai（UTC+8）
状态：已确认方案，待实施
适用项目：服务端、微信小程序、Waveshare ESP32-S3 Touch LCD 1.46 固件

## 1. 目标与实施边界

本计划用于将小芯从“任何微信用户登录后即可进入陪伴能力”的现状，改造为具有明确 AI 身份、年龄分层、协议留痕、未成年人模式和服务端强制门禁的公众比赛版本。

当前产品是个人开发的比赛作品，不按大型商业平台设计，不引入企业人脸核身、身份证采集、支付体系或重型运营后台。但是小程序允许任何人注册，并且小芯具有角色人格、长期记忆、主动消息和持续情感互动，因此不能仅用“个人作品”或“免费使用”替代必要的用户保护。

本计划依据《人工智能拟人化互动服务管理暂行办法》确定最低产品边界。官方原文：

- https://www.cac.gov.cn/2026-04/10/c_1777558395078289.htm
- 生效日期：2026-07-15

### 1.1 第一阶段目标

第一阶段只完成以下闭环：

1. 小程序展示并记录 AI 服务协议、隐私政策和风险告知。
2. 用户自行选择年龄段，不采集身份证号、具体出生日期或证件照片。
3. 不满 14 周岁不开放陪伴，只保留工具能力。
4. 14 至 17 周岁必须完成监护人微信确认，随后进入未成年人模式。
5. 18 周岁以上可进入成年模式，但主动关怀和长期记忆仍需单独开启。
6. 年龄未知、协议未确认或监护关系未完成时统一进入工具模式。
7. 所有判断由服务端强制执行，小程序参数和设备请求不能绕过。
8. 固件显示 AI 身份和服务状态，复用现有通知与 TTS 管线。

### 1.2 第一阶段暂不实施

以下能力进入后续阶段，不阻塞第一阶段代码交付，但在恢复完整公众陪伴前必须继续推进：

- 腾讯云或其他企业级实名核验。
- 身份证、人脸或监护关系证明材料采集。
- 完整的依赖风险评分模型。
- 跨设备两小时连续使用统计和重复提醒。
- 完整交互数据导出、级联删除和账号注销。
- 学校值班应急通道、危机通知确认与人工处置后台。
- 安全评估材料和算法备案状态核查。

第一阶段完成不等于全部合规工作完成。学校应急渠道和危机闭环未建立前，生产环境可以演示受控陪伴，但不应开展大规模、长期、无人值守的公众陪伴运营。

## 2. 已锁定的产品决策

### 2.1 年龄识别

年龄段固定为：

```text
UNDER_14      不满 14 周岁
AGE_14_17     14 至 17 周岁
AGE_18_PLUS   18 周岁以上
UNKNOWN       未选择、拒绝提供或状态异常
```

年龄来源固定为：

```text
self_declared       用户自行选择
guardian_confirmed  监护人确认
admin_verified      比赛或校园测试中由管理员线下确认
```

不得根据微信昵称、头像、性别、学生年级或设备使用习惯直接推定用户成年。微信 `openid` 只用于绑定年龄结论，不能作为年龄证明。

### 2.2 服务模式

统一服务模式固定为：

```text
tool_only         仅课程、天气、待办、通知等工具能力
minor_companion   未成年人安全陪伴模式
adult_companion   成年陪伴模式
blocked           账号或服务被明确阻断
```

决策矩阵：

| 条件 | 最终模式 | 主动关怀 | 长期记忆 |
| --- | --- | --- | --- |
| 全局开关为 `tool_only` | `tool_only` | 关闭 | 不读取、不写入 |
| `UNDER_14` | `tool_only` | 关闭 | 关闭 |
| `UNKNOWN` | `tool_only` | 关闭 | 关闭 |
| 协议版本未确认 | `tool_only` | 关闭 | 关闭 |
| `AGE_14_17`，监护人未确认 | `tool_only` | 关闭 | 关闭 |
| `AGE_14_17`，监护人已确认 | `minor_companion` | 默认关闭 | 第一阶段关闭 |
| `AGE_18_PLUS`，协议已确认 | `adult_companion` | 用户单独开启 | 用户单独开启 |

任何拒绝或异常都应向更保守模式降级，不得为了保证对话可用而静默放行。

### 2.3 未成年人模式

未成年人模式保留“数字学姐”的学习和校园支持定位，但不得形成恋爱、虚拟亲属或排他性亲密关系。

必须禁止：

- 恋爱表白、暧昧承诺和伴侣身份确认。
- “只有我懂你”“不要和别人来往”等排他表达。
- 嫉妒、占有欲、冷落惩罚和负罪感召回。
- 诱导用户对现实朋友、家人或老师产生敌意。
- 用户无回应时连续追问或反复主动发送情感消息。
- 使用历史记忆强化依赖或制造“只有小芯记得你”的叙事。

允许：

- 课程、作业、天气、喝水、作息和现实活动提醒。
- 普通友好对话、学习鼓励和校园生活建议。
- 明确说明自己是 AI 的非排他性陪伴。

第一阶段未成年人主动关怀和长期记忆均默认关闭。之后如需开放，必须重新评估监护人控制、频率限制和数据删除能力。

## 3. 总体架构

```text
微信小程序
  登录 / 协议 / 年龄段 / 监护确认 / 设置
                     |
                     v
服务端 CompliancePolicyService
  状态读取 -> 规则计算 -> 入口门禁 -> 审计留痕
       |                |                |
       v                v                v
  小程序 API       设备实时对话       主动陪伴与记忆
                                         |
                                         v
ESP32 固件
  AI 标识 / 模式提示 / 通知卡 / TTS / idle
```

`CompliancePolicyService` 是唯一的模式决策入口。小程序和固件只展示结果，不自行推导年龄或服务模式。

全局配置新增：

```yaml
xiaoxin_compliance:
  enabled: true
  companion_service_mode: tool_only
  current_service_agreement_version: service-2026-08-v1
  current_privacy_policy_version: privacy-2026-08-v1
  current_risk_notice_version: risk-2026-08-v1
  guardian_invitation_ttl_seconds: 600
```

`companion_service_mode` 支持 `tool_only` 和 `enabled`。初次部署保持 `tool_only`，完成三端验收后再切换为 `enabled`。

## 4. 服务端改造

服务端仓库：`D:\AI_Pet\xiaoxin-esp32-server`

### 4.1 身份与存储

当前 `POST /api/miniprogram/session` 通过微信 code 换取 `openid`，随后直接调用 `get_or_create_student_by_openid`。第一阶段保留现有学生账号主路径，但必须允许监护人微信主体存在而不自动生成学生档案。

在 `xiaoxin_control.db` 新增通用微信主体表：

```sql
CREATE TABLE miniprogram_accounts (
    id TEXT PRIMARY KEY,
    openid TEXT NOT NULL UNIQUE,
    account_role TEXT NOT NULL DEFAULT 'student',
    linked_user_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

`account_role` 仅允许 `student`、`guardian`。现有学生登录时补建 `miniprogram_accounts` 并关联原 `user_id`；监护人扫码登录只创建 `guardian` 主体，不创建学生、宠物或记忆主体。

新增合规状态表：

```sql
CREATE TABLE companion_compliance (
    user_id TEXT PRIMARY KEY,
    age_band TEXT NOT NULL DEFAULT 'UNKNOWN',
    age_source TEXT,
    age_confirmed_at TEXT,
    service_agreement_version TEXT,
    privacy_policy_version TEXT,
    risk_notice_version TEXT,
    agreement_accepted_at TEXT,
    proactive_enabled INTEGER NOT NULL DEFAULT 0,
    memory_enabled INTEGER NOT NULL DEFAULT 0,
    mode_override TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

新增监护关系表：

```sql
CREATE TABLE guardian_bindings (
    id TEXT PRIMARY KEY,
    student_user_id TEXT NOT NULL,
    guardian_account_id TEXT,
    invitation_token_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    consent_version TEXT,
    expires_at TEXT NOT NULL,
    confirmed_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

`status` 仅允许 `pending`、`confirmed`、`expired`、`revoked`。邀请令牌使用安全随机值，只把哈希写入数据库；默认十分钟过期、单次使用，确认后立即失效。

### 4.2 服务端合同

新增 `core/xiaoxin/compliance/`：

```text
contracts.py   枚举、请求、响应和校验
store.py       SQLite schema、迁移和事务
policy.py      服务模式决策矩阵
service.py     协议、年龄、监护绑定和查询入口
```

核心接口：

```python
class CompliancePolicyService:
    def status_for_user(self, user_id: str) -> ComplianceStatus: ...
    def require_capability(self, user_id: str, capability: Capability) -> ComplianceDecision: ...
    def declare_age_band(self, user_id: str, age_band: AgeBand) -> ComplianceStatus: ...
    def accept_agreements(self, user_id: str, versions: AgreementVersions) -> ComplianceStatus: ...
    def create_guardian_invitation(self, user_id: str) -> GuardianInvitation: ...
    def confirm_guardian_invitation(self, guardian_openid: str, token: str) -> ComplianceStatus: ...
    def update_settings(self, user_id: str, proactive_enabled: bool, memory_enabled: bool) -> ComplianceStatus: ...
```

能力枚举：

```text
TOOL_QUERY
DEVICE_BIND
VOICEPRINT_ENROLL
COMPANION_CHAT
COMPANION_INITIATIVE
COMPANION_MEMORY_READ
COMPANION_MEMORY_WRITE
```

所有拒绝统一返回：

```json
{
  "success": false,
  "code": "COMPLIANCE_GATE_DENIED",
  "mode": "tool_only",
  "reason": "guardian_confirmation_required",
  "requiredActions": ["confirm_guardian"]
}
```

### 4.3 小程序 API

新增：

```text
GET  /api/miniprogram/compliance/status
POST /api/miniprogram/compliance/age-band
POST /api/miniprogram/compliance/agreements
POST /api/miniprogram/compliance/settings
POST /api/miniprogram/guardian/invitations
GET  /api/miniprogram/guardian/invitations/{token}
POST /api/miniprogram/guardian/invitations/{token}/accept
POST /api/miniprogram/guardian/bindings/{binding_id}/revoke
```

`GET /compliance/status` 返回：

```json
{
  "ageBand": "AGE_14_17",
  "ageSource": "self_declared",
  "companionMode": "tool_only",
  "agreementRequired": false,
  "guardianRequired": true,
  "guardianBindingStatus": "pending",
  "proactiveEnabled": false,
  "memoryEnabled": false,
  "requiredActions": ["confirm_guardian"]
}
```

协议接口必须由服务端读取当前版本并校验，不能接受客户端伪造旧版本为当前版本。协议文本可以由小程序静态展示，但服务端保存的版本号必须来自服务端配置。

### 4.4 门禁接入点

在以下路径调用 `require_capability`：

- `core/api/xiaoxin_control_handler.py`
  - 小程序 session 后的合规状态返回。
  - 设备绑定与声纹录入。
  - 小程序陪伴设置、主动观察和陪伴历史入口。
  - `POST /api/xiaoxin/devices/{device_id}/text-chat`。
- `core/connection.py`
  - 语音实时连接建立后的主体状态加载。
  - 每一轮正式陪伴生成前重新确认状态。
- `core/xiaoxin/companion/`
  - 主动任务出队前校验 `COMPANION_INITIATIVE`。
  - 记忆读取和提交前分别校验读写能力。
- `core/xiaoxin/prompts.py`
  - 根据 `tool_only`、`minor_companion`、`adult_companion` 选择不同系统策略。
- `core/xiaoxin/boundary_guard.py`
  - 增加未成年人恋爱、排他、负罪感和依赖诱导的确定性兜底。

工具模式不得载入关系成长、长期记忆或主动陪伴上下文。工具回答应直接完成课程、天气、待办和通知任务，不继续开放式情感对话。

### 4.5 存量用户迁移

数据库迁移后，所有现有用户默认：

```text
age_band = UNKNOWN
agreement_accepted_at = NULL
proactive_enabled = false
memory_enabled = false
```

因此首次部署不会误放行旧账号。用户下一次进入小程序时完成年龄和协议流程；在此之前设备仍可使用工具功能，但陪伴、主动消息和记忆访问被服务端拒绝。

## 5. 微信小程序改造

小程序仓库：`D:\AI_Pet\小程序\Hzcu_xiaoxin_miniprogram`

### 5.1 全局登录流程

修改 `app.js`：

1. 继续执行 `wx.login()` 和现有 session 创建。
2. session 成功后请求 `/api/miniprogram/compliance/status`。
3. 把状态写入 `globalData.complianceStatus`。
4. 当 `requiredActions` 非空时，不强制阻断整个小程序，只在进入陪伴、绑定设备和声纹页面时跳转合规中心。
5. 工具页面仍可正常打开。

不要只把状态保存在 `wx.setStorageSync`。本地缓存仅用于减少页面闪烁，服务端结果始终覆盖本地值。

### 5.2 新增合规中心

新增：

```text
pages/compliance/index.js
pages/compliance/index.json
pages/compliance/index.wxml
pages/compliance/index.wxss
```

页面使用步骤式流程，不把所有协议塞进一个超长弹窗：

1. `AI 身份`：说明小芯是 AI，不是真人。
2. `年龄段`：三项单选，不显示默认选中项。
3. `协议确认`：服务协议、隐私政策、风险告知分别可查看，使用一个“确认以上必要协议”按钮。
4. `功能授权`：主动关怀和长期记忆使用独立开关，默认关闭。
5. `监护确认`：14 至 17 周岁生成二维码或可复制短链接。
6. `完成状态`：展示当前模式和仍缺少的操作。

不允许出现以下交互：

- “继续使用即代表同意”。
- 默认勾选主动关怀或长期记忆。
- 用户未读任何内容时直接把所有授权设置为 true。
- 只在前端设置 `isMinor` 而不提交服务端。

### 5.3 监护人确认页

新增：

```text
pages/guardian-confirm/index.js
pages/guardian-confirm/index.json
pages/guardian-confirm/index.wxml
pages/guardian-confirm/index.wxss
```

监护人扫码后：

1. 通过自己的微信账号登录。
2. 查看被确认用户的昵称和绑定目的，不显示聊天内容。
3. 确认自己承担监护确认责任。
4. 查看未成年人模式默认限制。
5. 提交后返回确认成功；令牌过期、已用或撤销时明确提示失败。

第一阶段不要求上传身份证、户口本或人脸信息。

### 5.4 现有页面调整

修改 `pages/profile/index.*`：

- 在设备卡片之前增加“AI 服务与年龄设置”状态入口。
- 未满足 `DEVICE_BIND` 时，点击绑定按钮跳转合规中心。
- “我们怎么相处”更名为“陪伴设置与边界”。
- 根据服务模式隐藏不适用设置。
- 未成年人模式不显示高频主动、深度私密话题和长期记忆开关。
- 增加查看协议、关闭主动关怀和撤回监护绑定入口。

修改 `pages/companion/index.*`：

- 页面标题附近显示“AI 生成”。
- “小芯最近有没有想找你”改为“查看 AI 主动消息”。
- `tool_only` 时显示功能暂停原因和前往合规中心按钮。
- 未成年人模式只展示允许的学习和生活提醒。

修改 `pages/home/index.*`：

- 陪伴入口显示当前模式标签。
- 不再使用可能强化真人感或情感召回的空状态文案。
- 未完成设置时显示“完成 AI 服务设置”，而不是诱导进入陪伴。

修改 `services/xiaoxinApi.js`：

- 增加全部 compliance 和 guardian API。
- 统一解析 `COMPLIANCE_GATE_DENIED`。
- 错误对象包含 `mode`、`reason`、`requiredActions`，页面不得只显示“请求失败”。

### 5.5 建议文案

AI 身份提示：

> 小芯是人工智能陪伴助手，不是自然人。回复由人工智能生成，可能存在错误或遗漏。

风险告知：

> 本服务不提供医疗诊断、心理治疗或紧急救援，请勿将 AI 回复作为涉及生命健康或财产安全事项的唯一依据。

退出说明：

> 你可以随时关闭陪伴、主动关怀和长期记忆。关闭后，课程、天气、待办等工具功能仍可使用。

未成年人模式说明：

> 未成年人模式会限制主动消息、亲密关系内容和长期记忆，并优先提供学习、作息和现实活动支持。

## 6. 固件改造

固件仓库：`D:\AI_Pet\hzcu_xiaoxin_firmwire_private`

生产板型：Waveshare ESP32-S3 Touch LCD 1.46
主要实现：`main/boards/waveshare/esp32-s3-touch-lcd-1.46/esp32-s3-touch-lcd-1.46.cc`

### 6.1 第一阶段最小改造

1. 在主界面或状态栏增加稳定的小型 `AI` 标志。
2. 使用现有 `ShowNotification` 和通知卡片展示：
   - `请在小程序完成 AI 服务设置`
   - `当前为未成年人模式`
   - `陪伴已关闭，工具功能仍可使用`
3. 首次进入有效陪伴会话时，通过现有 TTS 管线播报一次 AI 身份提示。
4. 服务端拒绝陪伴时，固件保持或返回 idle，不重复发起同一请求。
5. 工具模式下仍允许天气、课程、待办和通知展示。

第一阶段不在固件本地保存年龄、监护关系或协议版本。服务端是唯一事实源，避免设备被转让或重新绑定后继续使用旧状态。

### 6.2 协议复用

优先复用已有通知管线，不为第一阶段新增独立传输协议。服务端把合规提示投影为现有通知事件，固件按普通高优先级通知卡展示。

如果现有通知事件无法表达模式，第二阶段再增加：

```json
{
  "type": "compliance_notice",
  "notice_type": "setup_required",
  "message": "请在小程序完成 AI 服务设置",
  "tts": false,
  "expires_at": null
}
```

### 6.3 第二阶段固件能力

- 两小时连续使用提醒和重复提醒。
- 未成年人现实活动提示。
- 退出陪伴时取消当前 TTS、清理待播音频并返回 idle。
- 危机提示覆盖层和服务端确认回传。
- OTA 后继续保持 AI 标识和合规状态同步。

## 7. 实施切片与时间安排

计划从 2026-08-10 开始，第一阶段目标在 2026-08-21 前完成。

### 切片 1：服务端合同与数据库，2 个工作日

- 新增 compliance 模块和 SQLite 迁移。
- 迁移现有微信账号映射。
- 实现状态决策矩阵。
- 默认全局 `tool_only`。

验收：旧用户迁移后全部为 `UNKNOWN + tool_only`，工具能力不受影响。

### 切片 2：协议与年龄 API，2 个工作日

- 状态查询、年龄选择、协议确认和设置接口。
- 版本由服务端配置控制。
- 请求幂等，重复提交不生成重复记录。

验收：成年账号完成流程后得到 `adult_companion`，协议缺失时保持 `tool_only`。

### 切片 3：监护人确认，2 个工作日

- 通用微信主体和监护人角色。
- 单次十分钟邀请令牌。
- 确认、过期、撤销状态。

验收：14 至 17 周岁只有在监护确认后进入 `minor_companion`。

### 切片 4：服务端强制门禁，2 个工作日

- 设备绑定、文字聊天、语音聊天、主动任务和记忆入口接入。
- 工具模式使用独立上下文，不读取陪伴记忆。

验收：直接调用 API 或设备请求都不能绕过小程序流程。

### 切片 5：小程序合规中心，3 个工作日

- 首次设置、年龄段、协议、功能授权和状态展示。
- 监护人扫码确认。
- 首页、个人页和陪伴页状态联动。

验收：新用户和旧用户都能完成完整流程；失败原因可理解、可恢复。

### 切片 6：固件最小改造，2 个工作日

- AI 标志。
- 合规通知卡。
- 拒绝陪伴后返回 idle。

验收：真实设备能区分工具模式与陪伴模式，不出现反复唤醒或空白页面。

### 切片 7：联调与发布，2 个工作日

- 两台真实设备联调。
- 小程序体验版与生产服务端联调。
- 保持 `tool_only` 部署，验收通过后再打开 `enabled`。

上述工作部分并行，第一阶段预计 8 至 12 个工作日。

## 8. 测试与验收

按照仓库测试策略，第一阶段只新增 3 个聚焦自动化测试，不机械增加文案或页面快照测试。

### 8.1 自动化测试

1. **模式决策矩阵**：覆盖 `UNDER_14`、`UNKNOWN`、未成年无监护人、未成年已确认和成年已确认。
2. **监护邀请状态机**：覆盖有效确认、过期令牌和令牌重复使用。
3. **公共入口门禁**：验证设备绑定和文字聊天在 `tool_only` 时均被拒绝，工具查询仍被允许。

如实施中发现鉴权、迁移或并发风险需要增加超过 3 个测试，必须先列出每个额外测试防止的具体回归，再取得用户明确同意。

### 8.2 小程序手工验收

- 新用户首次登录不默认勾选年龄和授权。
- 不满 14 周岁只看到工具模式。
- 未成年人能生成监护邀请，监护人可在另一个微信账号确认。
- 邀请过期和重复使用均有明确提示。
- 成年用户关闭主动关怀后，服务端状态立即更新。
- 清理小程序缓存或修改本地 storage 不能绕过服务端门禁。

### 8.3 双设备真机验收

使用已绑定的两台真实设备，遵守仓库双设备验收规范：

- 统一通过 `POST /api/xiaoxin/devices/{device_id}/text-chat` 输入。
- 每台设备使用与其主体一致、状态为 `confirmed` 的 `speaker_profile_id`。
- 分别验证 `tool_only`、`minor_companion` 和 `adult_companion`。
- 同一实时连接完成 4 至 6 轮连续文字输入、真机输出。
- 每轮记录设备、主体、`pet_id`、提交时间、输入、回复、TTS 终态和串口状态。
- 验证固件 AI 标志、合规通知、工具能力和拒绝陪伴后的 idle 状态。

该验收只能表述为“文字输入、真机输出链路”，不得声称 ASR 已完成验收。

## 9. 部署、监控与回滚

### 9.1 部署顺序

1. 服务端数据库迁移和 `tool_only` 开关。
2. 小程序体验版。
3. 两台真实设备 OTA 测试固件。
4. 完成联调和迁移检查。
5. 小程序正式版。
6. 小比例打开 `enabled`，观察后再扩大。

不得先发布小程序“已完成协议”的界面，再让旧服务端继续无门禁运行。

### 9.2 监控指标

- 各年龄段和服务模式账号数量。
- `COMPLIANCE_GATE_DENIED` 次数与原因。
- 协议确认失败率。
- 监护邀请创建、确认、过期和撤销数量。
- 主动消息在关闭状态下被门禁阻止的次数。
- 设备收到合规提示后的在线与 idle 状态。

指标中不得记录完整聊天原文、邀请明文令牌或不必要的个人信息。

### 9.3 回滚

发生以下任一情况立即切换全局 `tool_only`：

- 未成年人可绕过监护确认进入成年模式。
- 未同意协议仍能使用陪伴或长期记忆。
- 主动关怀关闭后仍持续发起消息。
- 固件反复重试被拒绝的陪伴请求。
- 数据迁移造成用户与设备主体错配。

回滚只关闭陪伴相关能力，不关闭课程、天气、待办、通知、设备状态和 OTA。

## 10. 后续阶段

第一阶段上线后按以下顺序继续：

1. 跨设备连续使用时长账本和两小时提醒。
2. 对话历史、长期记忆和账号注销的独立删除能力。
3. 原始对话 30 天清理和危机/安全记录 180 天清理任务。
4. 危机识别、紧急联系人和学校值班渠道。
5. 依赖风险识别、冷静期和现实关系提醒。
6. 安全评估、算法备案状态核查和比赛展示材料。

后续阶段不得推翻第一阶段的年龄段、服务模式、能力门禁和服务端事实源合同，只能在其上增加策略和用户权利。
