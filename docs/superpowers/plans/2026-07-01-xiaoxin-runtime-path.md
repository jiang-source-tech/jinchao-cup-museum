# 小芯运行路径迁移实施计划

本文是 2026-07-01 的历史实施计划归档。原目标是把上游小智运行路径迁移为小芯私有路径，重点覆盖 OTA、WebSocket、管理接口和固件默认地址。

## 目标

让服务端和固件默认使用 `/xiaoxin` 路径，避免新设备误连上游 `/xiaozhi` 路径。

## 实施任务

1. 修改 Python OTA 路由前缀，启用 `/xiaoxin/ota/` 和 `/xiaoxin/ota/download/{filename}`。
2. 修改服务配置和管理接口前缀，让小芯路径成为当前运行路径。
3. 修改固件 OTA 默认地址，指向小芯私有服务端。
4. 更新文档中的 OTA 和 WebSocket 地址。
5. 扫描工作区内剩余 `xiaozhi` 引用，区分必须迁移和允许保留的上游命名。

## 验收

- OTA 地址使用 `/xiaoxin/ota/`。
- WebSocket 地址使用 `/xiaoxin/v1/`。
- 固件默认 OTA URL 不再引用上游 `/xiaozhi/ota/`。
- 允许保留的 `xiaozhi` 仅限历史包名、容器名、上游目录或兼容代码。

## 当前状态

路径迁移已基本完成，但真实 OTA 升级闭环还需要真机记录。当前状态由 `docs/development/xiaoxin-ota-websocket-paths.md` 和 `XIAOXIN-002` 继续跟踪。
