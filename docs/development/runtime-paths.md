# 运行路径说明

小芯当前使用 `/xiaoxin` 作为主动运行路径。上游 `/xiaozhi` 路径只作为历史兼容或归档资料存在。

## 当前运行路径

本地或服务器直连时，默认路径如下：

```text
WebSocket: ws://SERVER_IP:8000/xiaoxin/v1/
OTA:       http://SERVER_IP:8003/xiaoxin/ota/
控制台:    http://SERVER_IP:8003/xiaoxin/control/
```

如果通过域名和反向代理暴露公网服务，路径通常变为：

```text
WebSocket: wss://DOMAIN/xiaoxin/v1/
OTA:       https://DOMAIN/xiaoxin/ota/
控制台:    https://DOMAIN/xiaoxin/control/
```


`8003` is the Python HTTP port for OTA, vision analysis and the Xiaoxin control console.

## 配置位置

- Python 服务监听端口：`main/xiaozhi-server/config.yaml`
- Python 启动日志：`main/xiaozhi-server/app.py`
- OTA 处理器：`main/xiaozhi-server/core/api/ota_handler.py`
- HTTP 路由：`main/xiaozhi-server/core/http_server.py`
- WebSocket 服务：`main/xiaozhi-server/core/websocket_server.py`
- 固件默认 OTA：`D:\AI_Pet\hzcu_xiaoxin_firmwire_private\sdkconfig.defaults`

## 陪伴记忆 V2 运行路径

服务端记忆路径已经完成 replace-not-layer 切换：

```text
XiaoxinRuntime
  -> CompanionMind.prepare_turn / commit_turn
  -> data/xiaoxin_companion.db

/api/xiaoxin/memory-subjects/{subject_id}/memory
  -> CompanionMind.project

/api/xiaoxin/memory-subjects/{subject_id}/memory/control
  -> CompanionMind.apply_control

后台 worker
  -> CompanionMind.run_due_work
  -> ReflectionModel（线程隔离、失败可重试）
  -> InitiativeScheduler（异步机会扫描与原子 claim）
       -> LLMInitiativeComposer（只接收安全 brief）
       -> InitiativeDeliveryPort
            -> 现有 XiaoxinEventDispatcher / MQTT / WebSocket / TTS
```

- 唯一默认事务事实源是 `main/xiaozhi-server/data/xiaoxin_companion.db`。
- `memory_subject_id` 继续隔离说话人；personal pet 归微信主体所有。
- 学生资料中的当前年级是 `xiaoxin_age` 唯一事实源，未知为 `null`。
- 旧 `core.xiaoxin.memory`、旧 `memory_dir`、旧文件控制入口和运行时 importer 已删除。
- 小程序 UI 与固件尚未宣称已消费全部 V2 投影；服务端投影接口完成不等于端侧体验验收完成。
- 主动陪伴默认 `companion_initiative_scheduler_enabled=false`、`companion_initiative_delivery_enabled=false`；先单独开启 scheduler 做 dry-run，审核 opportunity 和阻挡原因后才允许同时开启模型 worker 与真实 delivery。

## 允许保留的 `xiaozhi` 名称

以下继承名称可以暂时保留：

- Java 包名，例如 `xiaozhi.*`。
- 目录名，例如 `main/xiaozhi-server`。
- 数据库名，例如 `xiaozhi_esp32_server`。
- 上游 GitHub 链接。
- `docs/upstream-archive/` 下的归档文档。

这些不是设备运行路径，不需要为了第一阶段产品化强行重命名。

## 路径审计命令

在仓库根目录运行：

```powershell
$runtimeFiles = @(
  "main\xiaozhi-server\core\http_server.py",
  "main\xiaozhi-server\core\api\ota_handler.py",
  "main\xiaozhi-server\config.yaml",
  "main\xiaozhi-server\app.py",
)
$runtimeText = ($runtimeFiles | ForEach-Object { Get-Content -Raw $_ }) -join "`n"
if ($runtimeText -match "/xiaozhi/ota/|/xiaozhi/v1/|context-path:\s*/xiaozhi|'/xiaozhi'") { throw "发现旧运行路径 /xiaozhi" }
if ($runtimeText -notmatch "/xiaoxin/ota/") { throw "缺少 /xiaoxin/ota/" }
if ($runtimeText -notmatch "/xiaoxin/v1/") { throw "缺少 /xiaoxin/v1/" }
```

期望结果：没有异常。

## 详细闭环文档

OTA、WebSocket、反向代理、固件发布和验收清单见：

```text
docs/development/xiaoxin-ota-websocket-paths.md
```
