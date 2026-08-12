# `121.43.33.0` 博物馆服务部署方案

## 状态

- 编制日期：2026 年 8 月 10 日
- 目标服务器：`121.43.33.0:22`，用户 `jiang`
- 当前状态：已完成旧项目隔离、博物馆服务部署和模拟文本链路验收；真机验收待执行
- 范围边界：本方案只涉及 `121.43.33.0`，不涉及任何历史迁移目标服务器

## 切换前取证结果

1. 旧项目位于 `/opt/xiaoxin-work/xiaoxin-esp32-server`，Git 远端仍是旧项目仓库。
2. 旧服务、Doorbell、MySQL、Voiceprint 和迁移代理容器均已停止，当前没有业务端口监听。
3. 旧活动数据目录含 `xiaoxin_control.db`，其中保存 9 门课程和 4 条待办；旧配置曾启用课程与待办调度器。
4. 旧仓库工作区存在未提交的 `main/xiaozhi-server/data/.config.yaml`，不得覆盖、回退或用于新项目快进更新。
5. 根分区约 40 GB，2026 年 8 月 10 日读取时使用率为 90%，构建镜像前必须重新检查可用空间。
6. 旧数据已有 2026 年 8 月 6 日备份，但正式切换前仍需生成新的、带校验值的只读备份。

## 生产部署拓扑

以下参数已经用户确认：

| 项目 | 实际值 |
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
| 进程存活入口 | `GET http://127.0.0.1:8003/museum/health/live` |
| RAG 就绪入口 | `GET http://127.0.0.1:8003/museum/health/ready` |
| OTA 入口 | `GET http://127.0.0.1:8003/museum/ota/` |

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

## 生产启动命令

生产密钥只保存在 Git 跟踪之外的 `/opt/jinchao-cup-museum/.env`，文件权限为 `0600`。Compose 文件位于子目录，因此必须显式传入根目录环境变量文件，并从生产代码目录执行：

```bash
cd /opt/jinchao-cup-museum
MUSEUM_DATA_DIR=/opt/jinchao-cup-museum-data \
MUSEUM_IMAGE_TAG=<服务端提交短SHA> \
docker compose \
  --env-file /opt/jinchao-cup-museum/.env \
  -f main/xiaozhi-server/docker-compose.yml \
  up -d --build museum-server
```

该命令不是旧项目部署命令，也不允许在 `/opt/xiaoxin-work/xiaoxin-esp32-server` 中执行。

## 2026 年 8 月 10 日执行结果

1. 切换前备份位于 `/opt/jinchao-cup-museum-backups/pre-cleanup-20260810-182842`，备份目录权限限制为生产用户使用。
2. `xiaoxin-work-full.tar.gz` 的 SHA-256 为 `02c0954391393100e0b3c8830ab342fa0b27cb9e7d4f7598567fb57484302ea3`；`xiaoxin-mysql-recovery.tar.gz` 的 SHA-256 为 `819e14639e4704a59c83162dbc75c797e27f064a8d1bb677216dbca790642590`。两个归档均通过 `sha256sum -c` 和 tar 结构校验。
3. 六个旧容器及明确属于 Xiaoxin、Xiaozhi、Voiceprint 的旧镜像已移除。活动路径 `/opt/xiaoxin-work` 和 `/tmp/xiaoxin*` 已不存在；原始旧文件只保留在上述隔离备份中，不再作为运行目录或挂载源。
4. 首次功能部署和模拟验收使用的完整服务端提交为 `dfdd805446a9f17224f1482fb98e5e2146203ee5`。部署前本地 `HEAD`、GitHub `origin/main` 和服务器 `HEAD` 均为该提交，服务器工作区干净；实际镜像标签为 `jinchao-museum-server:dfdd805`。
5. 新代码目录为 `/opt/jinchao-cup-museum`，新数据目录为 `/opt/jinchao-cup-museum-data`。数据挂载为 `/opt/jinchao-cup-museum-data:/opt/jinchao-museum-server/data`。
6. 活动数据目录仅包含 `.config.yaml`、`.mcp_server_settings.json`、`.wakeup_words.yaml`、`museum_demo.db`、`museum_firmware_releases.db` 和 `museum_firmware/`，未发现旧数据库、旧知识目录或旧配置段。
7. 首次创建容器时，Compose 未自动读取仓库根目录 `.env`，进程因缺少 `DASHSCOPE_API_KEY` 进入重启。镜像已经构建完成，因此故障恢复时显式传入 `--env-file`，并使用 `up -d --force-recreate --no-build museum-server` 重建容器；上方带 `--build` 的命令用于后续常规部署。最终容器状态为 `running`、重启次数为 `0`，`8000` 和 `8003` 正常监听，服务器本机 OTA 健康入口返回 `/museum/v1/` WebSocket 地址。
8. 对最终重建后的完整当前容器日志执行检查，未发现 `Traceback`、启动异常、`Business runtime failed`，也未发现 `course_reminder`、`todo_reminder`、`student_courses`、`student_todos`、`XiaoxinControlRuntime`、Doorbell 或 Voiceprint 运行时标记。
9. 旧控制台 `/xiaoxin/control/`、旧小程序课程接口 `/api/miniprogram/courses`、旧设备管理接口 `/api/xiaoxin/devices`、旧 OTA 路径和 activation 别名均返回 `404`；非 `/museum/v1/` 的 WebSocket 握手也必须被拒绝。
10. 模拟设备 `deployment-smoke-e1b7c0b01334` 于 2026 年 8 月 10 日 18:56（Asia/Shanghai）通过 `/museum/v1/` 发送“你好，你是谁”。服务端原样返回 STT 文本，业务状态依次为 `retrieving`、`ready`，回答为“你好，我是金潮杯博物馆的现场语音讲解助手。你可以直接问我眼前这件展品，我会根据馆方审核资料回答。”
11. 对应数据库审计记录为 `grounding_status=conversational`、`guard_result=conversational_scope`，未出现“小芯、高等数学、课程、待办、提醒、学生、宠物”等旧业务词。该检查只证明模拟文字输入、博物馆业务运行时和 WebSocket TTS 文本状态链路可工作，不代表麦克风、ASR、音频内容、扬声器、屏幕或真机 TTS ACK 已验收。
12. `museum_firmware_releases.db` 当前 `firmware_releases=0`、`firmware_artifacts=0`。OTA 健康入口可用，但尚未发布任何固件，不能据此声明设备升级或真机迁移完成。
13. 最终复核时根分区约 40 GB，已用约 21 GB，可用约 17 GB，使用率 57%。旧项目备份保留，未执行全局 `docker system prune`。

## 部署后验收

1. 容器状态为 running，且镜像标签与服务端提交一致。
2. `GET http://127.0.0.1:8003/museum/health/live` 返回 `live=true`；该入口只证明进程存活。
3. `GET http://127.0.0.1:8003/museum/health/ready` 返回 `ready=true`。hybrid 模式必须同时通过 SQLite 完整性、已发布事实存在和 Qdrant 全量事实 ID、展品 ID、事实类型、revision、来源、模型、维度、索引版本、内容哈希一致性检查。
4. 运行 `verify_museum_knowledge_release.py --qdrant-url http://127.0.0.1:6333 --pretty`，保存 `release_id`、`content_set_hash`、事实数和校验结果。该命令失败时不得继续业务验收。
5. `GET http://127.0.0.1:8003/museum/ota/` 返回成功状态和 `/museum/v1/` WebSocket 地址。
6. `8000` 只接受 `/museum/v1/` WebSocket；旧传输路径、旧控制台、小程序和 Xiaoxin 业务接口必须不可达。
7. 通过生产文本接口执行至少一条 grounded、unsupported 和连续追问用例，并按 `request_id` 复核 interaction trace。

Compose 的容器 healthcheck 使用 `/museum/health/live`，不使用 readiness。索引重建或内容发布窗口内 readiness 可以暂时失败，但不得因此触发容器自动重启；readiness 用于部署门禁和停止切流。
8. 日志不出现课程、待办、学生、Doorbell、Overview、Voiceprint 或主动陪伴调度器。
9. 容器挂载源为 `/opt/jinchao-cup-museum-data`，目录中不存在禁止项。
10. 通过文字或模拟客户端验证“你好，你是谁”可以正常回答博物馆身份。
11. 真机验证必须另行记录设备标识、固件提交、服务端提交、连接地址、操作步骤和实际表现。

## 回滚

首次部署发生故障时：

1. 停止新 `jinchao-museum-server` 容器。
2. 保留新代码目录、数据目录、镜像、日志和失败现场用于分析。
3. 不启动旧课程/待办服务，不恢复旧调度配置。
4. 基础语音服务是否临时恢复，必须使用不含旧业务运行时的已验证镜像并另行确认。

后续部署发生故障时，切回上一个已验收的 `jinchao-museum-server:<提交短 SHA>` 镜像，并恢复该版本对应的博物馆数据库备份。回滚只恢复博物馆服务能力，不恢复任何旧学生、课程、待办或提醒能力。
