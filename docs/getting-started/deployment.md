# 部署说明

本文记录小芯第一阶段推荐部署路径。

## 目标服务器

推荐配置：

- CPU：4 核
- 内存：8 GB
- GPU：不需要
- 系统：Ubuntu Server
- 模式：Docker 单服务部署

如果目标是尽快获得可用语音响应，不要在这台服务器上跑本地 LLM、本地 TTS 或重型本地 ASR。服务器应该专注于编排、WebSocket、OTA、Xiaoxin 控制台、MQTT 和轻量 VAD。

## 端口

- `8000`：ESP32 WebSocket 服务。
- `8003`：小芯 Python 服务 HTTP 接口，包含 OTA、控制台和部分工具接口。
- `1883`：MQTT 门铃 broker，用于睡眠设备的唤醒通知。

设备测试前，先在云服务器安全组和系统防火墙中开放这些端口。

## 首次部署

在服务器上执行：

```bash
cd /opt
git clone https://github.com/jiang-source-tech/xiaoxin-esp32-server.git xiaoxin-esp32-server
cd /opt/xiaoxin-esp32-server/main/xiaozhi-server
mkdir -p data
docker compose up -d
```

小芯第一阶段使用云端 ASR。不要为首跑路径准备本地 ASR 模型目录。

Docker Compose 文件也会启动 `xiaoxin-doorbell-mqtt`。当前 MQTT 承担设备在线状态和 wake；Overview 上线后也通过同一 Broker 发送 QoS 1 retained 快照。提醒正文、TTS、ACK 和播放完成态仍然走 WebSocket。

## Xiaoxin Device MQTT First Release

- Configure `xiaoxin_control.doorbell_mqtt.endpoint` before commercial OTA.
- Configure the server publisher credential as `xiaoxin_control.doorbell_mqtt.username` and `xiaoxin_control.doorbell_mqtt.password`.
- Generate Mosquitto `password_file` and `acl_file` from the server credential store before starting the broker:
  `python -m core.xiaoxin.broker_auth --config data/.config.yaml --db data/xiaoxin_doorbell_credentials.db --out mosquitto/auth`
- Verify a fresh OTA response contains `doorbell_mqtt.enabled: true`, `doorbell_mqtt.version: 1`, endpoint, opaque device credential, `status_topic`, `notification_topic`, and optional `overview_topic`.
- Verify the ESP32 uses the exact endpoint, credential, and topic strings returned by OTA instead of deriving a tenant prefix.
- Verify the ESP32 publishes retained status to `device/{device_id}/status`.
- Verify wake publishes non-retained `{"type":"wake"}` to `device/{device_id}/notification`.
- After Overview rollout is enabled, verify QoS 1 retained snapshots publish to `device/{device_id}/overview`.
- Verify a student account cannot wake an unbound device and can act only on its own bound device.
- Verify student profile fields are treated as self-filled metadata and are ignored by wake and binding authorization.

## Xiaoxin Control Console

Open:

```text
http://SERVER_IP:8003/xiaoxin/control/
```

Console accounts, device bindings and delivery data use local SQLite in the Python service; MySQL, Redis and the removed Java manager service are not required.


仅 Docker 内部联调时，`endpoint` 可以是 `xiaoxin-doorbell-mqtt:1883`。真机 ESP32 需要使用设备可访问的 `SERVER_IP:1883`。

重启服务端容器：

```bash
docker restart xiaozhi-esp32-server
docker logs -f xiaozhi-esp32-server
```

## 设备 URL

服务端和 ESP32 设备应使用：

```text
WebSocket: ws://SERVER_IP:8000/xiaoxin/v1/
OTA:       http://SERVER_IP:8003/xiaoxin/ota/
MQTT:      SERVER_IP:1883
```

如果使用公网域名和 TLS，则使用：

```text
WebSocket: wss://DOMAIN/xiaoxin/v1/
OTA:       https://DOMAIN/xiaoxin/ota/
```

## 验证

运行：

```bash
docker ps
docker logs -n 80 xiaozhi-esp32-server
docker logs -n 80 xiaoxin-doorbell-mqtt
```

Before starting or restarting `xiaoxin-doorbell-mqtt`, regenerate broker auth files from the live credential DB:

```bash
cd /opt/xiaoxin-esp32-server/main/xiaozhi-server
python -m core.xiaoxin.broker_auth --config data/.config.yaml --db data/xiaoxin_doorbell_credentials.db --out mosquitto/auth
```

期望结果：

- `xiaoxin-doorbell-mqtt` 正在运行并监听 `1883` 端口。
- ESP32 能拿到 `/xiaoxin/v1/` WebSocket 地址。
- ESP32 能拿到 `/xiaoxin/ota/` OTA 地址。

## 上游参考

原始全模块部署文档已归档到：

```text
docs/upstream-archive/original-docs/Deployment_all.md
```
