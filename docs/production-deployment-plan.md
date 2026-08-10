# `121.43.33.0` 博物馆服务部署方案

## 状态

- 编制日期：2026 年 8 月 10 日
- 目标服务器：`121.43.33.0:22`，用户 `jiang`
- 当前状态：待用户确认部署目录、数据目录、端口和 HTTP/TLS 方案后执行
- 范围边界：本方案只涉及 `121.43.33.0`，不涉及任何历史迁移目标服务器

## 已确认现状

1. 旧项目位于 `/opt/xiaoxin-work/xiaoxin-esp32-server`，Git 远端仍是旧项目仓库。
2. 旧服务、Doorbell、MySQL、Voiceprint 和迁移代理容器均已停止，当前没有业务端口监听。
3. 旧活动数据目录含 `xiaoxin_control.db`，其中保存 9 门课程和 4 条待办；旧配置曾启用课程与待办调度器。
4. 旧仓库工作区存在未提交的 `main/xiaozhi-server/data/.config.yaml`，不得覆盖、回退或用于新项目快进更新。
5. 根分区约 40 GB，2026 年 8 月 10 日读取时使用率为 90%，构建镜像前必须重新检查可用空间。
6. 旧数据已有 2026 年 8 月 6 日备份，但正式切换前仍需生成新的、带校验值的只读备份。

## 拟定部署拓扑

以下参数尚未执行，必须先得到用户明确确认：

| 项目 | 拟定值 |
| --- | --- |
| 代码目录 | `/opt/jinchao-cup-museum` |
| 数据目录 | `/opt/jinchao-cup-museum-data` |
| Git 仓库 | `git@github.com:jiang-source-tech/jinchao-cup-museum.git` |
| 部署分支 | `main` |
| Compose 文件 | `/opt/jinchao-cup-museum/main/xiaozhi-server/docker-compose.yml` |
| 容器名称 | `jinchao-museum-server` |
| WebSocket | `8000`，正式路径 `/museum/v1/` |
| HTTP/OTA | `8003`，正式路径 `/museum/ota/` |
| 镜像标签 | `jinchao-museum-server:<服务端提交短 SHA>` |
| 健康入口 | `GET http://127.0.0.1:8003/museum/ota/` |

代码目录和数据目录必须独立。不得把新仓库克隆到旧项目目录，也不得把旧项目 `data` 目录挂载给新容器。

## 数据白名单

新数据目录只允许包含博物馆运行所需内容：

- 经过清理的 `.config.yaml`
- `museum_demo.db` 或后续正式博物馆内容数据库
- `museum_firmware_releases.db`
- `museum_firmware/`
- 当前链路确实使用的 `.wakeup_words.yaml` 和 `.mcp_server_settings.json`

以下内容禁止迁移或挂载：

- `xiaoxin_*.db`、对应 WAL/SHM、锁文件和备份副本
- `xiaozhi_control.db`、`xiaozhi_companion.db`
- `student_courses`、`student_todos` 或提醒设置的任何导出
- `xiaoxin_knowledge/`、`xiaoxin_memory/`、`ota-inbox/`
- `xiaoxin_control`、`xiaoxin_runtime`、`voiceprint` 配置段
- Doorbell 凭据、旧 MQTT 数据和旧小程序配置

当前服务端会在启动时检查这些边界。活动挂载中发现旧数据或旧配置段时，进程直接拒绝启动，不再只输出警告。

## 部署前检查

1. 重新读取本地 `HEAD`、GitHub `origin/main` 和服务器目标目录 `HEAD`。
2. 确认两个本地仓库工作区干净，远端地址分别指向博物馆服务端和固件仓库。
3. 确认服务器新代码目录不存在，或其工作区干净且只能快进更新。
4. 对旧 `data`、旧 `.config.yaml` 和当前 Docker 元数据创建时间戳备份，并记录 SHA-256。
5. 检查磁盘空间；不得自动执行 `docker system prune` 或删除旧备份。
6. 以白名单方式生成新 `.config.yaml`，不得整体复制旧配置。
7. 确认 `8000`、`8003` 未被占用，并确认防火墙与安全组策略。
8. 确认固件使用的 OTA 地址。当前固件受版本控制的默认 OTA 地址为空，正式构建必须显式配置真实 `/museum/ota/` 地址。

## 拟定启动命令

确认上述参数后，在新代码目录执行：

```bash
MUSEUM_DATA_DIR=/opt/jinchao-cup-museum-data \
MUSEUM_IMAGE_TAG=<服务端提交短SHA> \
docker compose -f main/xiaozhi-server/docker-compose.yml up -d --build museum-server
```

该命令不是旧项目部署命令，也不允许在 `/opt/xiaoxin-work/xiaoxin-esp32-server` 中执行。

## 部署后验收

1. 容器状态为 running，且镜像标签与服务端提交一致。
2. `GET http://127.0.0.1:8003/museum/ota/` 返回成功状态和 `/museum/v1/` WebSocket 地址。
3. `8000` 只接受博物馆 WebSocket 路径，旧控制台和小程序接口不可达。
4. 日志不出现课程、待办、学生、Doorbell、Overview、Voiceprint 或主动陪伴调度器。
5. 容器挂载源为 `/opt/jinchao-cup-museum-data`，目录中不存在禁止项。
6. 通过文字或模拟客户端验证“你好，你是谁”可以正常回答博物馆身份。
7. 真机验证必须另行记录设备标识、固件提交、服务端提交、连接地址、操作步骤和实际表现。

## 回滚

首次部署发生故障时：

1. 停止新 `jinchao-museum-server` 容器。
2. 保留新代码目录、数据目录、镜像、日志和失败现场用于分析。
3. 不启动旧课程/待办服务，不恢复旧调度配置。
4. 基础语音服务是否临时恢复，必须使用不含旧业务运行时的已验证镜像并另行确认。

后续部署发生故障时，切回上一个已验收的 `jinchao-museum-server:<提交短 SHA>` 镜像，并恢复该版本对应的博物馆数据库备份。回滚只恢复博物馆服务能力，不恢复任何旧学生、课程、待办或提醒能力。
