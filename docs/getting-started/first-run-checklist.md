# 首次运行检查清单

用这份清单把小芯从部署状态推进到可用语音闭环。

## 服务端

- [ ] 已执行 `docker compose up -d --build`。

## 管理员

- [ ] 已注册第一个账号。
- [ ] 第一个账号可以作为管理员登录。

## 服务端配置

- [ ] `main/xiaozhi-server/data/.config.yaml` 使用本地配置，包含设备可访问的 `server.websocket` 和已选模型服务商。

- [ ] 修改 `.config.yaml` 后已执行 `docker restart xiaozhi-esp32-server`。
- [ ] `docker logs -f xiaozhi-esp32-server` 显示服务端正常运行。

## 模型服务商

- [ ] ASR 配置为 `AliyunBLStreamASR`。
- [ ] LLM 配置为 `AliLLM`。
- [ ] LLM 模型使用 `qwen-flash`。
- [ ] TTS 配置为 `AliBLTTS`。
- [ ] 首次延迟基线使用 `Memory: nomem`。
- [ ] 百炼 API Key 只填写到需要它的模型配置中。

## 运行 URL

- [ ] `server.websocket` 为：

```text
ws://SERVER_IP:8000/xiaoxin/v1/
```

- [ ] `server.ota` 为：

```text
http://SERVER_IP:8003/xiaoxin/ota/
```

如果走公网 TLS，则改为：

```text
wss://DOMAIN/xiaoxin/v1/
https://DOMAIN/xiaoxin/ota/
```

## ESP32

- [ ] 设备和服务器处于可互相访问的网络路径。
- [ ] 设备固件或配置指向 Xiaoxin OTA URL。
- [ ] 设备可以通过 OTA 获取 Xiaoxin WebSocket URL。
- [ ] 唤醒并提问后，设备能播放语音回复。

## 基线记录

第一次跑通后记录：

```text
日期：
服务器区域：
ASR：
LLM：
TTS：
主观平均响应时间：
已知问题：
```
