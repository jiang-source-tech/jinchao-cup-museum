# 备份与升级

本文记录小芯服务端升级前后的最低安全动作。重点不是把所有东西都备份一遍，而是确保配置、SQLite 数据库、上传文件和模型资产不会在升级时被覆盖。

## 需要重点保护的路径

Docker 单服务部署时，优先保护这些目录：

```text
data/.config.yaml
uploadfile/
models/
main/xiaozhi-server/data/xiaoxin_companion.db
```

含义：

- `data/.config.yaml` 保存服务端本地运行配置、模型供应商、OTA 和 WebSocket 参数。
- `uploadfile/` 保存后台上传文件、音频、固件包或其他运行资产。
- `models/` 保存本地模型文件。如果当前小芯部署只使用云端模型，也仍建议记录该目录是否为空。
- `main/xiaozhi-server/data/xiaoxin_companion.db` 是陪伴记忆 V2 的唯一事务事实源。运行中不得用普通文件复制代替 SQLite 一致性备份。

## 陪伴记忆 V2 切换前归档

旧记忆不做语义迁移。升级到 V2 前，先停止旧服务，把旧 `data/xiaoxin_memory/` 目录及其中可能存在的 `xiaoxin_memory.db` 保存为只读归档。该归档只用于审计和旧版本回滚，不在运行时导入，也不按文件名推断 owner、pet 或 `memory_subject_id`。

在 `main/xiaozhi-server` 目录执行 PowerShell：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = Join-Path "backups" "xiaoxin-memory-v1-$stamp"
New-Item -ItemType Directory -Force -Path $archive | Out-Null
if (Test-Path "data/xiaoxin_memory/") {
  Copy-Item -Recurse -Force "data/xiaoxin_memory/" (Join-Path $archive "xiaoxin_memory")
}
Get-ChildItem -Recurse -File $archive | ForEach-Object { $_.IsReadOnly = $true }
Get-ChildItem -Recurse -File $archive |
  Get-FileHash -Algorithm SHA256 |
  Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $archive "SHA256SUMS.csv")
```

Linux：

```bash
stamp="$(date +%Y%m%d-%H%M%S)"
archive="backups/xiaoxin-memory-v1-$stamp"
mkdir -p "$archive"
if [ -d data/xiaoxin_memory ]; then
  cp -a data/xiaoxin_memory "$archive/xiaoxin_memory"
fi
find "$archive" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > "$archive/SHA256SUMS"
chmod -R a-w "$archive"
```

如果旧 SQLite 位于 `data/xiaoxin_memory/xiaoxin_memory.db`，它必须随目录一起归档。不要把它复制成 V2 的 `data/xiaoxin_companion.db`，两者 schema 和产品语义不同。

V2 首次启动会创建 `data/xiaoxin_companion.db`。已有 V2 数据库升级前使用 SQLite 在线备份：

```powershell
sqlite3 data/xiaoxin_companion.db ".backup 'backups/xiaoxin_companion-before-upgrade.db'"
```

```bash
sqlite3 data/xiaoxin_companion.db ".backup 'backups/xiaoxin_companion-before-upgrade.db'"
```

## 最小备份命令

在 Linux 服务器上，先进入项目根目录，然后执行：

```bash
mkdir -p backups
tar -czf backups/xiaoxin-config-upload-$(date +%Y%m%d-%H%M%S).tar.gz data uploadfile
```


## 升级规则

不要直接用新版本示例配置覆盖旧的 `data/.config.yaml`。

正确做法是：

1. 备份旧配置。
2. 打开新版本示例配置。
3. 对比新增字段。
4. 只把需要的新字段合并到旧配置。
5. 保留已经验证过的密钥、模型供应商、OTA 地址和 WebSocket 地址。

尤其要确认：

```text
server.websocket
server.ota
selected_module
```

## 升级前检查清单

- [ ] 已备份 `data/.config.yaml`。
- [ ] 已备份 `uploadfile/`。
- [ ] 已记录当前 Docker 镜像 tag 或 Git commit。
- [ ] 已记录当前模型供应商配置。
- [ ] 从旧记忆版本切换时，已创建 `data/xiaoxin_memory/` 只读归档并保存哈希清单。
- [ ] 已确认旧 `xiaoxin_memory.db` 不会被改名或导入为 `data/xiaoxin_companion.db`。
- [ ] 已使用 SQLite `.backup` 备份现有 `data/xiaoxin_companion.db`，或确认这是全新首次启动。
- [ ] 已记录 `server.websocket`。
- [ ] 已记录 `server.ota`。
- [ ] 已确认固件当前连接的是 `/xiaoxin/ota/` 和 `/xiaoxin/v1/`。

## 升级后检查清单

- [ ] 小芯控制台能打开：`http://SERVER_IP:8003/xiaoxin/control/`。
- [ ] 服务端容器处于运行状态。
- [ ] 模型供应商配置仍存在。
- [ ] ESP32 能连接 WebSocket：`/xiaoxin/v1/`。
- [ ] OTA 地址返回正常：`http://SERVER_IP:8003/xiaoxin/ota/`。
- [ ] 日志里没有持续重启、模型鉴权失败或 TTS 错误。
- [ ] `data/xiaoxin_companion.db` 可以打开，`PRAGMA integrity_check` 返回 `ok`。
- [ ] `PRAGMA user_version` 返回当前 schema 版本 `19`。
- [ ] 已记录 `pending`、`retry` 和 `failed` 整理任务数量；不存在持续增长的失败队列。

陪伴记忆 V2 最小健康检查，在 `main/xiaozhi-server` 执行：

```powershell
sqlite3 data/xiaoxin_companion.db "PRAGMA integrity_check; PRAGMA user_version; SELECT status, COUNT(*) FROM consolidation_jobs GROUP BY status ORDER BY status;"
```

```bash
sqlite3 data/xiaoxin_companion.db 'PRAGMA integrity_check; PRAGMA user_version; SELECT status, COUNT(*) FROM consolidation_jobs GROUP BY status ORDER BY status;'
```

判断标准：数据库能打开、完整性为 `ok`、schema 版本为 `19`；少量 `pending` 或处于退避期的 `retry` 可以正常存在，持续增长的 `failed`、同一 job 反复重试、initiative opportunity 长期停在 `claimed`/`delivering` 或数据库无法打开才是发布阻断。v11 升级到 v12 时创建的历史 `xiaoxin_companion.db.pre-v12.bak` 只用于当时的迁移恢复；当前发布必须另做 SQLite 在线备份，并在迁移后执行 `integrity_check` 与 `foreign_key_check`。

## Qwen 前台与 DeepSeek 后台

当前模型分工不可混淆：`selected_module.LLM=AliLLM` 的 Qwen 负责前台主对话；
`xiaoxin_runtime.companion_worker_llm=DeepSeekLLM` 的 DeepSeek 只负责语义记忆解释、
陪伴反思和主动消息生成三个后台任务。三个任务统一使用版本化 Prompt、JSON Output、
严格 Parser、Evidence 校验和最多一次结构修复，模型不直接读写 Store。

所有真实凭据只从进程环境读取：DeepSeek 使用 `DEEPSEEK_API_KEY`，百炼主对话、ASR、
VLLM 和 TTS 使用 `DASHSCOPE_API_KEY`，地图服务使用 `XIAOXIN_AMAP_API_KEY`，声纹服务
完整健康检查 URL 使用 `XIAOXIN_VOICEPRINT_URL`。Compose
只透传这些变量；`data/.config.yaml` 不得出现 `sk-` 密钥或地图密钥。已提交到 Git
历史的旧密钥必须在供应商控制台吊销并重新签发，修改 YAML 不能替代供应商侧轮换。

## DeepSeek Harness 发布门禁

在 `main/xiaozhi-server` 执行 `scripts/xiaoxin_companion_harness.py`。固定顺序为：

1. `prepare` 检查精确 Git SHA、干净工作树、环境变量、数据库、DeepSeek 和两个串口。
2. `model-eval` 直接通过生产适配器运行 M01-M10、R01-R02、I01。
3. `deploy` 使用相同 SHA 二次确认，在线备份 SQLite，并以独立镜像标签部署候选。
4. `hil-run` 用管理员评测模式驱动两台设备，等待 TTS `done`，采集 HTTP 与串口证据。
5. `collect`、`review-packet` 生成数据库审计、确定性报告和固定 Codex 评分规程。
6. Codex 只提交 `PASS`、`FAIL` 或 `INCONCLUSIVE` 的 `codex-judge-report.json`。
7. `finalize` 合并结果；只有最终 `PASS` 才能 `promote`，其他结果必须 `restore`。

关键命令合同示例：

```powershell
python scripts/xiaoxin_companion_harness.py prepare --run-dir RUN --run-id RUN_ID --git-sha SHA --config data/.config.yaml --database data/xiaoxin_companion.db --compose-file docker-compose.yml --device-a-port COM4 --device-b-port COM6
python scripts/xiaoxin_companion_harness.py deploy --run-dir RUN --confirm-sha FULL_SHA
python scripts/xiaoxin_companion_harness.py model-eval --run-dir RUN
$env:XIAOXIN_HARNESS_ADMIN_SESSION = "ADMIN_SESSION"
python scripts/xiaoxin_companion_harness.py hil-run --run-dir RUN --base-url https://SERVER --device-a-id DEVICE_A --device-b-id DEVICE_B --subject-a SUBJECT_A --subject-b SUBJECT_B
python scripts/xiaoxin_companion_harness.py collect --run-dir RUN
python scripts/xiaoxin_companion_harness.py review-packet --run-dir RUN
python scripts/xiaoxin_companion_harness.py finalize --run-dir RUN --judge-report CODEX_REPORT
python scripts/xiaoxin_companion_harness.py promote --run-dir RUN
python scripts/xiaoxin_companion_harness.py restore --run-dir RUN --confirm-run-id RUN_ID
```

管理员 Session 只通过 `XIAOXIN_HARNESS_ADMIN_SESSION` 进入 `hil-run` 进程，不作为
命令行参数，也不写入证据包。证据包只允许隔离测试主体和
合成事实；生产用户原文、Cookie、API Key 和完整 Prompt 输入不得进入提交物。

## CP-SEM 语义记忆渐进发布

语义画像和自然召回使用独立开关，不会联动开启主动调度或真实投递：

```yaml
xiaoxin_runtime:
  companion_worker_enabled: true
  companion_memory_interpreter_mode: "off"
  companion_memory_active_explicit_release_enabled: false
```

发布顺序固定为 `off -> shadow -> candidate -> active_explicit`：

1. `off`：不运行语义解释，保持发布前行为。
2. `shadow`：后台解释并记录无原文诊断，不写 Evidence。
3. `candidate`：只写 `prompt_eligible=false` 的待确认候选；小程序二级“记忆与隐私”入口可以消费安全摘要并确认、拒绝、纠正或删除。
4. `active_explicit`：只允许明确、本人、低敏感、稳定且无冲突的白名单事实自动激活。即使把 mode 配成 `active_explicit`，只要 `companion_memory_active_explicit_release_enabled` 仍为 `false`，服务端就会强制按 `candidate` 执行。

每次切换后检查 operator 中的 `semantic_memory_evaluations`、`jobs_by_status`、`temporary_context_messages` 和 `temporary_context_pins`。自动发布门禁还会在 backlog 超限或近期错误率越界时，把 `active_explicit` 降为 `candidate`，把 `candidate` 降为 `shadow`。错误率降级后，最近 10 个终态任务全部成功且 backlog 未越界时自动恢复配置模式；任何新失败都会立即打断该连续成功恢复条件。

语义模块或模型初始化失败时，对话、提醒和设备功能继续运行，但自然画像失败关闭，不恢复正则画像主写路径。最快回滚只需要把配置改为：

```yaml
companion_memory_interpreter_mode: "off"
companion_memory_active_explicit_release_enabled: false
```

重启服务后不需要反向迁移 schema；已经确认的 Evidence 仍由 v12 生命周期和用户控制处理。不要把候选或 v12 Evidence 回写到旧正则结构。

## 回滚原则

如果升级后设备无法连接，先不要继续改固件。优先回滚服务端配置或容器版本，并确认旧版本仍能跑通。

只有当旧版本也无法恢复时，再怀疑服务器网络、安全组、域名、TLS 或设备侧持久化配置。

陪伴记忆 V2 回滚必须是单向代码回滚：

1. 停止新版本服务，禁止继续写 `data/xiaoxin_companion.db`。
2. 把当前 V2 DB 另存为只读故障现场，不删除、不覆盖。
3. 恢复升级前记录的旧 Git commit 或容器镜像及对应配置。
4. 仅向旧版本挂载升级前的 `data/xiaoxin_memory/` 只读归档副本；需要恢复写入时，先从只读归档复制出新的工作副本。
5. 验证旧版本启动、身份隔离、对话和提醒后再恢复流量。

禁止把 V2 Evidence 反向写回旧 JSON、JSONL 或旧 `xiaoxin_memory.db`。V2 运行期间产生的新记忆在旧版本中不可见，这是干净切换的已知回滚边界，不允许用临时转换脚本制造第三套事实源。
