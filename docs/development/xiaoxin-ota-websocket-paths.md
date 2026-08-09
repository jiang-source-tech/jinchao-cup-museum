# Xiaoxin OTA 与 WebSocket 路径闭环

本文专门记录 `XIAOXIN-002`：小芯私有 OTA 与 WebSocket 路径。

结论先写清楚：路径迁移本身已经基本完成，但完整产品闭环还需要一次真实固件发布和真机 OTA 验收记录。因此状态工作台中它仍标为“部分完成”，不是“已完成”。

## 职责边界

OTA 负责让设备拿到版本信息、固件下载地址和 WebSocket 连接地址。

WebSocket 负责设备和服务端之间的实时语音、文本事件、TTS 音频、通知投递和 ACK。

两者关系如下：

```text
ESP32 固件
  └─ 请求 OTA: /xiaoxin/ota/
       ├─ 获取新版本信息
       ├─ 获取固件下载地址
       └─ 获取 WebSocket 地址: /xiaoxin/v1/

ESP32 固件
  └─ 连接 WebSocket: /xiaoxin/v1/
       ├─ hello 握手
       ├─ 音频上行
       ├─ TTS 音频下行
       ├─ xiaoxin_event 下发
       └─ xiaoxin ACK 回传
```

## 当前服务端路由

Python 服务端启动后会监听两个端口：

```text
WebSocket: 8000
HTTP:      8003
```

当前关键路由：

```text
GET/POST/OPTIONS  /xiaoxin/ota/
GET/OPTIONS       /xiaoxin/ota/download/{filename}
WebSocket         /xiaoxin/v1/
HTTP 控制台        /xiaoxin/control/
控制 API           /api/xiaoxin/...
```

相关代码：

```text
main/xiaozhi-server/app.py
main/xiaozhi-server/core/http_server.py
main/xiaozhi-server/core/websocket_server.py
main/xiaozhi-server/core/api/ota_handler.py
```

## 当前固件配置

固件默认 OTA 地址在：

```text
D:\AI_Pet\hzcu_xiaoxin_firmwire_private\sdkconfig.defaults
```

当前默认值：

```text
CONFIG_OTA_URL="http://121.43.33.0:8003/xiaoxin/ota/"
```

固件还包含旧路径清理逻辑。如果 NVS 中持久化过旧的 `xiaozhi/ota` 地址，固件会忽略并清除旧值，再回退到 `CONFIG_OTA_URL`。

相关代码：

```text
D:\AI_Pet\hzcu_xiaoxin_firmwire_private\main\ota.cc
```

相关测试：

```text
D:\AI_Pet\hzcu_xiaoxin_firmwire_private\tests\ota_url_config_test.py
```

## 开发环境地址

本地或服务器直连时使用：

```text
OTA:       http://SERVER_IP:8003/xiaoxin/ota/
WebSocket: ws://SERVER_IP:8000/xiaoxin/v1/
```

这里 `SERVER_IP` 必须是 ESP32 能访问到的地址。不要在设备配置里使用 `127.0.0.1`。

## 公网反向代理地址

如果通过公网域名和 TLS 暴露服务，推荐对外使用：

```text
OTA:       https://DOMAIN/xiaoxin/ota/
WebSocket: wss://DOMAIN/xiaoxin/v1/
```

反向代理需要把路径转发到内部服务：

```text
/xiaoxin/ota/       -> http://127.0.0.1:8003/xiaoxin/ota/
/xiaoxin/control/   -> http://127.0.0.1:8003/xiaoxin/control/
/api/xiaoxin/       -> http://127.0.0.1:8003/api/xiaoxin/
/xiaoxin/v1/        -> ws://127.0.0.1:8000/xiaoxin/v1/
```

WebSocket 反代必须保留升级头：

```text
Upgrade
Connection
Host
X-Forwarded-For
X-Forwarded-Proto
```

## README 中地址的含义

README 里可能记录公网演示地址，例如：

```text
OTA接口地址: https://2662r3426b.vicp.fun/xiaoxin/ota/
Websocket接口地址: wss://2662r3426b.vicp.fun/xiaoxin/v1/
```

固件默认配置里可能使用服务器直连地址，例如：

```text
http://121.43.33.0:8003/xiaoxin/ota/
```

这两类地址不是矛盾：

- README 公网地址用于浏览器、演示和带 TLS 的部署入口。
- 固件默认地址用于当前直连服务器的开发和测试。

如果正式发版给普通用户，应优先使用域名和 HTTPS/WSS，避免 IP 变更导致固件重新烧录。

## OTA 响应检查

启动服务后，用浏览器或 PowerShell 检查：

```powershell
Invoke-WebRequest -Uri http://SERVER_IP:8003/xiaoxin/ota/ -UseBasicParsing
```

期望：

- HTTP 服务可访问。
- 返回内容包含设备能识别的 OTA 信息。
- 返回的 WebSocket 地址是 `/xiaoxin/v1/`，不是 `/xiaozhi/v1/`。
- 固件下载地址如果存在，路径应位于 `/xiaoxin/ota/download/` 下。

如果使用公网域名：

```powershell
Invoke-WebRequest -Uri https://DOMAIN/xiaoxin/ota/ -UseBasicParsing
```

期望：

- TLS 证书有效。
- 反向代理没有改写错路径。
- 返回内容中的 WebSocket 地址使用 `wss://`。

## WebSocket 检查

服务启动日志应出现类似信息：

```text
Websocket地址是 ws://SERVER_IP:8000/xiaoxin/v1/
```

设备连接后，服务端日志应能看到：

- 新 WebSocket 连接。
- `hello` 握手。
- 音频或 listen 状态消息。
- TTS 下行或事件下发。

如果设备无法连接，优先检查：

- 云服务器安全组是否开放 `8000`。
- 本机防火墙是否开放 `8000`。
- OTA 返回的 WebSocket 地址是否为设备可访问地址。
- 公网部署时是否使用 `wss://`。
- 反向代理是否支持 WebSocket upgrade。

## 固件发布检查清单

每次准备发布新固件前，记录以下信息：

```text
发布日期：
固件版本：
目标板：
构建命令：
OTA 地址：
WebSocket 地址：
固件包路径：
固件包大小：
测试设备 MAC：
```

发布前确认：

- [ ] 固件 `CONFIG_OTA_URL` 指向 `/xiaoxin/ota/`。
- [ ] 固件不会默认引用上游 `/xiaozhi/ota/`。
- [ ] 旧 NVS 中的 `/xiaozhi/ota/` 会被清理。
- [ ] 服务端 OTA 接口可访问。
- [ ] OTA 返回的 WebSocket 地址可被设备访问。
- [ ] 新版本号大于当前设备版本。
- [ ] 固件包下载地址可访问。

发布后确认：

- [ ] 设备检测到新版本。
- [ ] 设备显示 OTA 更新提示。
- [ ] 设备能下载并安装固件。
- [ ] 升级后设备能重启进入新版本。
- [ ] 升级后设备仍能请求 `/xiaoxin/ota/`。
- [ ] 升级后设备仍能连接 `/xiaoxin/v1/`。

## 什么时候可以把 XIAOXIN-002 改成已完成

满足以下条件后，状态工作台里的 `XIAOXIN-002` 才能从“部分完成”改成“已完成”：

- 服务端和固件默认路径全部统一为 `/xiaoxin`。
- 开发直连地址和公网域名地址的用途已经写清楚。
- OTA 接口检查通过。
- WebSocket 真机连接检查通过。
- 至少完成一次真实固件 OTA 升级，并记录结果。
- 旧 `/xiaozhi/ota/` 持久化配置迁移通过测试。

在没有真实 OTA 升级记录前，最多只能说“路径迁移完成”，不能说“OTA 发布闭环完成”。
