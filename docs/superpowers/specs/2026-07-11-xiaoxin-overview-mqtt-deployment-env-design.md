# Xiaoxin Overview MQTT Git/Docker 部署配置设计

## 目标

让服务器通过“本地提交 → GitHub → 生产服务器 `git pull` → Docker 重建”启用 MQTT Overview，同时不把生产 HMAC secret 写入 Git 历史。

本设计只解决当前 `PATCH /api/miniprogram/weather-location` 因 `overview_mqtt_disabled` 返回 HTTP 503 的部署配置问题，不改变天气、课程、待办或 MQTT payload 协议。

## 当前问题

- Git 跟踪的 `main/xiaozhi-server/data/.config.yaml` 没有覆盖 `xiaoxin_control.overview_mqtt`。
- 默认 `config.yaml` 明确设置 `overview_mqtt.enabled: false`，因此生产服务器拉取新代码后仍创建 `DisabledOverviewSyncService`。
- Docker Compose 已将宿主机 `./data` 挂载到容器，但没有传入 Overview HMAC 环境变量。
- 若直接把真实 `ip_hmac_secret` 写入 `.config.yaml` 并提交，秘密会永久进入 Git 历史。

## 设计决策

### 1. Git 中显式启用 Overview

在受版本控制的 `main/xiaozhi-server/data/.config.yaml` 的现有 `xiaoxin_control` 节点下增加：

```yaml
overview_mqtt:
  enabled: true
  db: data/xiaoxin_overview.db
  ip_hmac_secret: ""
  trusted_proxy_cidrs: []
  retry_tick_seconds: 1
  daily_refresh_hour: 0
  daily_refresh_minute: 5
```

空的 YAML secret 是安全占位符，不是生产秘密。启用后，手动省市保存可以初始化真实 Overview store/service，不再被 feature gate 直接返回 503。

### 2. HMAC secret 使用环境变量覆盖

运行时从环境变量 `XIAOXIN_OVERVIEW_IP_HMAC_SECRET` 读取部署秘密：

- 环境变量为非空字符串时，优先于 YAML `ip_hmac_secret`。
- 环境变量缺失或为空时，保留现有 YAML 行为，兼容测试和已有部署。
- 不记录 secret 内容；诊断只允许报告是否已配置。

服务器将真实值保存在仓库外的 Docker Compose `.env`：

```dotenv
XIAOXIN_OVERVIEW_IP_HMAC_SECRET=<至少 32 字节的随机值>
```

### 3. Docker Compose 透传

`docker-compose.yml` 和 `docker-compose_all.yml` 的 `xiaozhi-esp32-server` 服务都传入：

```yaml
- XIAOXIN_OVERVIEW_IP_HMAC_SECRET=${XIAOXIN_OVERVIEW_IP_HMAC_SECRET:-}
```

部署必须使用 `docker compose up -d --build`，确保新代码进入镜像；仅 `docker restart` 不足以更新镜像代码。

## 数据流

```text
服务器 .env 中的 HMAC secret
  -> Docker Compose 环境变量
  -> create_xiaoxin_control_runtime()
  -> OverviewSyncService(ip_hmac_key=...)

Git 跟踪的 data/.config.yaml enabled=true
  -> config_loader 深度合并
  -> 创建 Overview store/provider/service
  -> 小程序手动省市 PATCH 不再命中 overview_mqtt_disabled
```

## 失败行为

- 环境变量未配置：手动省市仍可工作；自动公网 IP 定位保持 fail-closed，并在诊断中报告 HMAC 未配置。
- Open-Meteo 不可达：手动保存返回可重试的 503 `weather location validation unavailable`，不能伪装为配置成功。
- Broker 未连接：位置和快照可持久化为 pending，但不能声称硬件已经同步。
- `.env` 中的秘密不得打印、提交或写入验收截图。

## 测试

1. 配置合同测试确认受控部署配置显式设置 `overview_mqtt.enabled=true`，默认模板仍保留安全默认值。
2. 运行时测试确认非空环境变量覆盖 YAML secret。
3. 运行时测试确认环境变量缺失/空值时回退到 YAML secret。
4. 静态合同测试确认两个 Docker Compose 文件均透传环境变量。
5. 运行完整 `tests/xiaoxin` 和 `git diff --check`。

## 部署验收

服务器拉取并重建后，容器内安全检查应显示：

```text
enabled=True
hmac_configured=True
```

随后小程序手动保存省市应返回 HTTP 200；这只证明服务端位置链路成功。硬件总览仍需记录 MQTT revision、PUBACK、retained payload 和屏幕结果。
