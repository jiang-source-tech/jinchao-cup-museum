# 故障排查

本文记录小芯部署和定制过程中最常见的故障。排查顺序应从服务是否启动、端口是否开放、配置是否一致开始，不要一上来改模型或固件。

## 小芯控制台打不开

先检查容器状态和服务端日志：

```bash
docker ps
```

再确认云服务器安全组和本机防火墙已开放 `8003`。

浏览器访问地址应为：

```text
http://SERVER_IP:8003/xiaoxin/control/
```

不要在浏览器里使用容器内地址或 `127.0.0.1`，除非浏览器就在同一台服务器上运行。

## 服务端容器反复重启

查看服务端日志：

```bash
docker logs -n 160 xiaozhi-esp32-server
```

常见原因：

- 模型供应商 API Key 缺失或无效。
- 配置文件缩进错误，导致 YAML 解析失败。

## ESP32 无法连接 WebSocket

先检查这些项：

- 云服务器安全组允许 `8000`。
- 本机防火墙允许 `8000`。
- `server.websocket` 是设备能访问的地址。
- 直连部署使用 `ws://SERVER_IP:8000/xiaoxin/v1/`。
- 公网 TLS 部署使用 `wss://DOMAIN/xiaoxin/v1/`。
- 服务端日志里能看到 WebSocket 启动信息。

如果 OTA 返回的 WebSocket 地址是 `127.0.0.1`，设备必然连不上。`127.0.0.1` 对 ESP32 来说是设备自己，不是服务器。

## OTA 地址错误

在 `data/.config.yaml` 或其他本地配置文件中检查 `server.ota`。

直连部署应为：

```text
http://SERVER_IP:8003/xiaoxin/ota/
```

公网 TLS 部署应为：

```text
https://DOMAIN/xiaoxin/ota/
```

如果设备仍收到旧的 `/xiaozhi/ota/` 路径：

1. 保存正确的 `server.ota`。
2. 重启服务端容器。
3. 确认 OTA 响应里不再出现 `/xiaozhi/ota/`。
4. 确认固件 NVS 中旧 OTA 地址会被清理。

## 没有语音播报

先检查 TTS 配置：

- TTS 供应商是否为当前推荐的 `AliBLTTS`。
- API Key 是否有效。
- 声音名称是否被当前模型支持。
- 服务端日志是否出现 TTS 鉴权、限流或网络错误。

查看日志：

```bash
docker logs -n 160 xiaozhi-esp32-server
```

如果 ASR 和 LLM 都正常，只有 TTS 没声音，优先怀疑 TTS 配置，而不是 WebSocket。

## 响应慢

第一阶段不要追求复杂模型链路。建议基线：

- LLM 使用 `qwen-flash` 或同级低延迟模型。
- `max_tokens` 控制在 `150` 到 `300`。
- 关闭不必要插件。
- 使用云端流式 ASR 和流式 TTS。

如果仍然慢，按日志分段定位：

- ASR 慢：检查音频上传、ASR 供应商延迟和网络。
- LLM 慢：换低延迟模型，降低输出长度。
- TTS 慢：换声音或供应商，检查流式输出。
- 首包慢：检查服务器到供应商的网络质量。

陪伴记忆 V2 的 `prepare_turn` 和 `commit_turn` 只走本地 SQLite 与确定性策略，实时回复不会等待 ReflectionModel。若响应慢而日志只显示后台整理超时，不要禁用或删除记忆数据库；应继续定位 ASR、主对话 LLM、TTS 或网络。

## 陪伴记忆 V2 数据库或后台任务异常

唯一默认数据库是：

```text
main/xiaozhi-server/data/xiaoxin_companion.db
```

在 `main/xiaozhi-server` 运行：

```powershell
sqlite3 data/xiaoxin_companion.db "PRAGMA integrity_check; PRAGMA user_version; SELECT status, COUNT(*) FROM consolidation_jobs GROUP BY status ORDER BY status;"
```

预期：

- `PRAGMA integrity_check` 返回 `ok`。
- `PRAGMA user_version` 返回 `5`。
- `pending` 表示等待后台处理，`retry` 表示模型失败后处于退避期；二者不会阻塞聊天、提醒和设备能力。
- `failed` 持续增加时，检查 ReflectionModel 配置、网络、超时和返回 schema，不要把完整用户原文写入日志。

若数据库无法打开：

1. 停止服务，保留故障 DB，不要删除或覆盖。
2. 检查目录权限、磁盘空间和 SQLite 锁占用。
3. 使用升级前 `.backup` 副本恢复到新的工作文件。
4. 重新启动并复查 schema 版本和任务数量。

不要把旧 `data/xiaoxin_memory/`、旧 JSON/JSONL 或旧 `xiaoxin_memory.db` 导入 V2。旧数据只能作为只读归档或旧代码回滚输入。
