# 小芯管理员记忆控制台运维说明

## 部署顺序

1. 备份身份数据库和陪伴记忆数据库。
2. 部署包含 `users.role` 和 `admin_audit_log` schema 的版本。
3. 现有安装显式提升本地控制台账号。不要修改设备、主体或 Evidence 归属。
4. 验证只有预期账号为 `admin`，再开放控制台。

在 `main/xiaozhi-server` 目录执行：

```powershell
python -m core.xiaoxin.identity.admin_cli --db <身份数据库路径> --username <本地账号>
```

命令可重复执行，并输出 `before` 与 `after` 角色。账号不存在时命令失败。

## 验证

- 普通用户通过 Cookie 或 Bearer 访问 `/api/xiaoxin/admin/*` 均返回 `403 admin_required`。
- 管理员列表可跨 owner 查看安全摘要，普通 `/api/xiaoxin/memory-subjects` 仍只返回本人主体。
- 管理员详情中的 owner、pet、profile 和 Evidence 均来自目标主体 owner。
- `admin_audit_log` 记录详情读取结果，但不包含声纹键、原始对话或学生资料原文。
- 当前在线且已确认的 `user_speaker` 主体优先于旧 `device_unknown` 主体。
- 管理员记忆写请求携带 `X-Xiaoxin-CSRF` 与幂等键，并在目标 owner 上下文执行。
- 跨 owner 主体合并返回 `403 cross_owner_merge_forbidden`。
- `admin_audit_log` 对写操作记录 actor、target、动作、幂等键和安全结果码。

## 回滚

1. 先回退控制台静态页面，停止调用 `/api/xiaoxin/admin/*`。
2. 管理员只读 API 可以停用，不影响普通用户 API。
3. 保留 `users.role` 与 `admin_audit_log`；不要删除或反向迁移主体、Epoch、Evidence。
4. 需要恢复数据库时使用部署前备份，不执行记忆归属重写。

当前阶段已开放管理员受控记忆修改和同 owner、同类型主体合并。高风险二次认证尚未实现；执行生产清除或关系重置前仍应核对审计与备份。
