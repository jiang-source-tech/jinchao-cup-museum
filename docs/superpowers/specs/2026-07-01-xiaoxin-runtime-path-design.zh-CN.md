# 小芯运行路径强替换设计

日期：2026-07-01

## 目标

将设备和浏览器实际访问的运行时前缀从 `/xiaozhi/...` 强替换为 `/xiaoxin/...`，并且只保留 `/xiaoxin/...` 路由。

本次改动服务于 `D:\Learn\hzcu-xiaoxin-firmwire` 固件项目，让设备通过新的 OTA 入口获取 WebSocket 地址和固件更新信息。

## 范围

需要修改的运行时访问点：

- Python OTA 接口：`/xiaozhi/ota/` 改为 `/xiaoxin/ota/`
- Python OTA 下载接口：`/xiaozhi/ota/download/{filename}` 改为 `/xiaoxin/ota/download/{filename}`
- OTA 返回的 WebSocket 地址：`ws://host:port/xiaozhi/v1/` 改为 `ws://host:port/xiaoxin/v1/`
- OTA 固件下载 URL 拼接：生成 `/xiaoxin/ota/download/{filename}`
- 固件默认 OTA URL：`CONFIG_OTA_URL` 改为 `/xiaoxin/ota/`
- 当前固件构建配置：`sdkconfig` 中的 `CONFIG_OTA_URL` 同步改为 `/xiaoxin/ota/`
- 管理后端 context path：`/xiaozhi` 改为 `/xiaoxin`
- 管理前端开发代理：`/xiaozhi` 改为 `/xiaoxin`

## 明确不做

以下内容不做重命名，避免破坏代码结构、依赖或外部引用：

- Java 包名 `xiaozhi.*`
- Python 目录名 `main/xiaozhi-server`
- 数据库名 `xiaozhi_esp32_server`
- `xiaozhi-fonts` 组件和相关构建参数
- 第三方项目名、GitHub 链接、文档中的历史说明
- 用户可见品牌文案，除非它同时是运行时访问 URL

## 架构

固件仍然按现有流程工作：

1. 固件启动后由 `Ota::GetCheckVersionUrl()` 读取 OTA 地址。
2. 固件请求 `/xiaoxin/ota/`。
3. 服务端 OTA handler 返回 `server_time`、`firmware` 和 `websocket` 配置。
4. 固件保存返回的 `websocket.url`，之后通过 WebSocket 打开语音通道。

服务端 WebSocket 握手逻辑不需要按路径分流。现有 WebSocket server 对请求 path 没有业务依赖，因此只需要确保 OTA 下发的新 URL 使用 `/xiaoxin/v1/`。

## 兼容性

这是强替换方案，不保留 `/xiaozhi/...` 兼容路由。

已烧录旧 OTA 地址的设备会继续请求 `/xiaozhi/ota/`，在新服务端上会失败。处理方式是重新烧录固件，或通过设备已有配置流程把 NVS 中的 `wifi.ota_url` 更新为 `/xiaoxin/ota/`。

## 错误处理

- 访问旧 `/xiaozhi/ota/` 会返回 404。
- OTA 配置缺省时，服务端自动生成的 WebSocket URL 必须使用 `/xiaoxin/v1/`。
- 固件下载 URL 必须使用 `/xiaoxin/ota/download/{filename}`，避免设备收到旧路径后下载失败。

## 测试

服务端验证：

- `GET /xiaoxin/ota/` 返回 OTA 健康信息，并显示 `/xiaoxin/v1/` WebSocket 地址。
- `POST /xiaoxin/ota/` 返回 JSON，其中 `websocket.url` 包含 `/xiaoxin/v1/`。
- `GET /xiaozhi/ota/` 不再命中 OTA 路由。
- 如果存在固件 bin，返回的 `firmware.url` 包含 `/xiaoxin/ota/download/`。

固件验证：

- `sdkconfig.defaults` 和 `sdkconfig` 中 `CONFIG_OTA_URL` 均使用 `/xiaoxin/ota/`。
- 重新编译后，固件日志中的 OTA 请求地址为 `/xiaoxin/ota/`。
- OTA 成功后，WebSocket 连接地址为 `/xiaoxin/v1/`。

管理端验证：

- manager-api 启动后的 context path 为 `/xiaoxin`。
- manager-web 开发代理使用 `/xiaoxin`。
- 登录、设备列表、参数管理等常用接口能通过新前缀访问。
