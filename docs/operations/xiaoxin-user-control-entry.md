# 小芯用户控制入口

阶段 2 为学生侧提供低负担的陪伴控制合同。小程序只需要发送用户语义动作，服务端自动从当前登录用户解析唯一的 confirmed `memory_subject`，再复用 `CompanionMind.apply_control`。客户端不得提交任意 `subject_id`，也不得直接写入 Companion Store。

## 接口

### `GET /api/miniprogram/companion/settings`

需要登录。返回当前用户的小芯表达投影和可用动作：

- `settings.learned_behaviors`：相处中学会的表达调整。
- `settings.explicit_settings`：用户明确设置的边界或长期相处方式，包含撤销所需的安全标识。
- `available_actions`：`correct`、`forget`、`do_not_mention`、`too_proactive`、`too_personal`、`disable_initiative`。

用户没有 confirmed 主体时返回 `409 confirmed_subject_required`；同一用户存在多个 confirmed 主体时返回 `409 subject_selection_required`，防止把控制写入错误设备或错误主体。

### `POST /api/miniprogram/companion/control`

请求体只需要用户动作。`idempotency_key` 可选，缺省时服务端生成；客户端重试时应复用服务端返回的 key。

```json
{"action":"do_not_mention","idempotency_key":"mini-control-1"}
```

动作映射如下：

| 用户动作 | CompanionMind 控制 |
| --- | --- |
| `correct` | `correct_evidence`，需要 `evidence_id` 和 `correction` 或 `replacement_content` |
| `forget` | 根据 `evidence_id` 或 `theme` 映射到 `forget_evidence` / `forget_theme` |
| `do_not_mention` | `set_boundary(memory_reference_depth=never)` |
| `too_proactive` | `set_interaction_contract(initiative_level=low)` |
| `too_personal` | `set_interaction_contract(memory_reference_depth=shallow)` |
| `disable_initiative` | `set_interaction_contract(initiative_level=disabled)` |

接口成功后返回 `mapped_action`、幂等键和底层控制结果。该入口不改变小芯出生气质，只改变当前有效表达权限或记忆使用边界。

## 验收

服务端聚焦回归：

```powershell
D:\AI_Pet\xiaoxin-esp32-server\.venv\Scripts\python.exe -m pytest `
  main\xiaozhi-server\tests\xiaoxin\test_companion_control_api.py -q `
  --basetemp=main\xiaozhi-server\tmp\pytest-stage2
```

必须确认：

1. 用户动作映射到确定性底层控制，投影能看到变化。
2. 未确认主体被拒绝。
3. 多个 confirmed 主体不被静默选取。

