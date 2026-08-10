# 使用唯一博物馆传输路径，独立运行博物馆业务

服务端与固件的正式合同只使用 `/museum/v1/` WebSocket 和 `/museum/ota/` OTA。服务端不注册旧项目路径、activation 别名或文件名固件下载接口，并在 WebSocket 握手阶段拒绝非正式路径。博物馆业务实现放在独立 `MuseumRuntime` 后面，由通用 `ConversationRuntime` interface 接入连接层，不在旧业务命名空间中继续叠加分支。
