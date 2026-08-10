# 旧项目残留审计与清理计划

## 文档状态

- 审计日期：2026 年 8 月 10 日
- 审计范围：
  - 服务端仓库：D:/AI_Pet/jinchao-cup-museum
  - 固件仓库：D:/AI_Pet/museum-firmwire
- 本轮性质：本地清理、生产只读取证与数据隔离加固；不执行 push、生产部署或 OTA 发布
- 总体置信水平：
  - 本地源码、数据库、构建产物与 Git 状态结论：高
  - `121.43.33.0` 旧部署配置、容器和数据库结论：高
  - 某一次历史“八点高等数学”播报对应的完整投递日志：未知

### 当前实施进度（2026 年 8 月 10 日）

- 服务端已断开旧 XiaoxinControlRuntime 启动链，并删除学生、陪伴、课程、待办、Doorbell、Overview、声纹和旧控制台实现。
- 服务端身份、问候、错误回退和预置语音生成文案已切换为金潮杯博物馆语义；“你好，你是谁”即使在设备尚未绑定当前展品时也能正常回答。
- 服务端完整测试结果为 `34 passed`；另行执行的配置、业务网关、博物馆问答与 OTA 聚焦集合为 `7 passed`。受版本控制的 `config.example.yaml` 已实际构造 WebSocket 与 OTA 处理器，认证关闭时不再要求 `auth_key`，认证开启但缺少密钥时会明确拒绝启动。
- 服务端数据边界已进一步收紧：活动挂载出现旧数据库、旧知识目录或旧课程/待办配置段时直接拒绝启动；Compose 默认改用独立 `museum-data` 挂载。对应聚焦测试为 `3 passed`。
- 固件 1.46 寸目标板已完成博物馆主界面源码替换，旧宠物、Doorbell、Overview、卡片分页和相关测试已移除；完整 Python 测试为 `227 passed`。
- 固件活动源码和测试扫描已不再发现宠物、Doorbell、Overview、课程/待办或声纹业务字符串；电源、电池、设置、低功耗和运行健康模块仅剩历史 `xiaoxin_*` 技术命名，重命名列为后续低风险整理。
- 固件旧宠物、课程、通知、Overview、启动动画和可视化设计资料已移入 `docs/legacy/`；当前 OTA、串口和电源排障文档已改为博物馆中性入口，串口提示符改为 `museum>`。
- 当前机器未加载 ESP-IDF：`IDF_PATH` 为空且找不到 `idf.py`，因此本轮没有执行新的固件构建，不能把现有 `build/` 产物当作本次改动的构建证据。
- 目标服务器已确认为 `121.43.33.0`。旧仓库、Compose、容器和数据挂载已经完成只读取证；旧容器当前停止，尚未执行新博物馆服务部署或 OTA 发布，设备实际固件 SHA 仍待真机确认。

阶段状态：阶段 0 的只读取证已完成，读取时目标服务器不存在运行中的旧 reminder loop；阶段 1、2、4 和 7 已完成主要源码清理与本地提交；阶段 3 的本地强制隔离已完成，生产数据目录切换待部署；阶段 6 的发布、部署和真机验收尚未执行。

下文第 1 至第 4 节保留实施前审计基线，不能把其中的“当前”理解为本地工作区最新状态；最新状态以上述实施进度和实际 Git 差异为准。

## 1. 执行摘要

当前问题不是“某一行文案没替换”，而是三个版本层次彼此不一致：

1. 服务端和固件的博物馆改造主要存在于本地未提交工作区。
2. 已提交版本和远端版本仍包含旧学生陪伴、课程提醒、待办提醒、Doorbell、Overview 和电子宠物逻辑。
3. 设备 OTA 发布库没有任何可用固件发布，设备持续上报 0.1.4，但拿不到新版本。

因此，只清理本地源码不能阻止线上旧实例继续提醒。生产环境必须先完成版本、配置、数据库和设备二进制取证，再进行清理和部署。

### 1.1 版本快照

| 仓库 | 本地 HEAD | origin/main | 工作区状态 |
| --- | --- | --- | --- |
| 服务端 | 17a6180，重建博物馆业务层并加入水晶杯演示 | d71f7b5 | 本地领先 1 个提交；另有 246 个文件变更，约删除 125336 行 |
| 固件 | 2802b32，初始化博物馆固件项目 | 2802b32 | 25 个文件变更，约删除 5006 行；新增 museum_state.* 尚未提交 |

服务端本地 HEAD 和 origin/main 均仍包含旧 XiaoxinControlRuntime。固件 HEAD 和 origin/main 均仍包含旧 Doorbell、Overview、课程/待办通知卡片和电子宠物主界面。

### 1.2 最高优先级结论（实施前基线）

| 编号 | 级别 | 结论 | 主要证据 | 置信 |
| --- | --- | --- | --- | --- |
| F-01 | P0 | 线上极可能仍运行旧服务端或旧配置 | origin/main 仍在 app.py 创建 create_xiaoxin_control_runtime；旧运行时包含课程调度循环 | 高 |
| F-02 | P0 | 本地博物馆切换尚未形成可部署版本 | 服务端工作区 246 个文件变更，约删除 125336 行；固件工作区 25 个文件变更，约删除 5006 行，均未提交 | 高 |
| F-03 | P0 | OTA 无法把本地博物馆固件送到设备 | museum_firmware_releases.db 中 firmware_releases=0、firmware_artifacts=0；0.1.4 的 18 次检查均为 no_eligible_release | 高 |
| F-04 | P0 | 实施前有效人设仍保留“小芯”身份 | agent-base-prompt.txt 首行仍为 You are Xiaoxin；answering.py 的身份和问候回复硬编码“我是小芯” | 高 |
| F-05 | P0 | 实施前固件仍运行电子宠物，而非博物馆页面 | CMake 仍编译 paopao_pet emotion、mood、behavior；目标板实现仍初始化并驱动宠物状态机；map 中存在对应符号 | 高 |
| F-06 | P0 | 实施前 museum_state 尚未真正作用到屏幕 | application.cc 解析成功后只记录日志；没有 ApplyMuseumState、SetMuseumState、RenderMuseum 或 BuildMuseumStateDisplayText 调用 | 高 |
| F-07 | P0 | 仓库内仍有会误部署旧项目的脚本 | docker-setup.sh 会下载 xinnan-tech/xiaozhi-esp32-server 的 Compose 文件，并操作 /opt/xiaozhi-server 和旧容器 | 高 |
| F-08 | P1 | 旧数据会被 Compose 持续保留 | 当前 Compose 将整个 ./data 挂载到容器，旧 xiaoxin_*.db 不会因重建镜像消失 | 高 |
| F-09 | P1 | “高等数学”示例仍保存在旧控制台 | xiaoxin_control.html 仍有高等数学默认值、课程提醒模板和语音文案 | 高 |
| F-10 | P1 | 实施前仓库仍有大量失效源码、测试和文档 | 固件仍有 Doorbell 源文件 10 个、xiaoxin 根级旧源文件 4 个、旧测试约 54 个、旧设计文档 40 余份 | 高 |

本地实施状态：F-04、F-05、F-06、F-07、F-09 和 F-10 对应的主要源码问题已修正，并已分别形成服务端与固件本地提交；提交尚未 push，也尚未部署或经过真机验收。

## 2. “八点高等数学”来源分析

### 2.1 已提交服务端中的完整触发链

已提交服务端具备以下链路：

    data/.config.yaml
      -> app.py 创建 XiaoxinControlRuntime
      -> control_runtime.py 启动 reminder loop
      -> XiaoxinCourseReminderScheduler 查询 student_courses
      -> 生成“小芯提醒你，……课……”的 speak_text
      -> XiaoxinEventDispatcher
      -> xiaoxin_event 卡片下发 + reliable TTS 播放
      -> 设备显示并播报

关键事实：

1. origin/main 的 app.py 仍导入并创建 create_xiaoxin_control_runtime。
2. 旧运行时默认使用 data/xiaoxin_control.db。
3. 课程调度器只有在以下配置为真时才运行：
   - xiaoxin_control.enabled
   - xiaoxin_control.course_reminder_scheduler_enabled
4. 自定义 data/.config.yaml 会递归覆盖默认 config.yaml。
5. Compose 挂载整个 data 目录，因此线上旧配置和旧课程记录会跨镜像重建保留。
6. 课程调度器的语音内容来自数据库课程名称，不要求固件内置“高等数学”字符串。

### 2.2 本地已经排除的来源

当前本地工作区不能自行产生这条课程提醒：

- 当前 app.py 已断开 XiaoxinControlRuntime 的创建和启动。
- 当前本地 xiaoxin_control.db 中：
  - student_courses=0
  - student_todos=0
  - student_course_reminder_settings=0
- 当前本机没有进程监听 8000 或 8003。
- 当前固件工作区已删除 xiaoxin_event 的课程、待办和 Overview 处理分支。
- 当前本地构建不包含课程提醒页面，但仍保留通用 TTS 播放能力。

这说明现场提醒不是由当前本地进程和当前本地空数据库触发。

### 2.3 `121.43.33.0` 已确认的旧部署事实

2026 年 8 月 10 日完成只读取证，结论如下：

1. 旧仓库位于 `/opt/xiaoxin-work/xiaoxin-esp32-server`，不是本项目 GitHub 仓库。
2. 旧 Compose 曾将整个旧 `data` 目录挂载到服务容器。
3. 旧默认配置中的 `xiaoxin_control.enabled`、课程调度器和待办调度器均为启用状态。
4. 旧 `xiaoxin_control.db` 中保存 9 门课程和 4 条待办；课程中明确包含“高等数学”。
5. 旧服务容器于 2026 年 8 月 6 日 10:22（Asia/Shanghai）正常退出。读取时 `8000`、`8003` 和 `1883` 没有监听进程，因此目标服务器当时不存在运行中的旧提醒循环。
6. 旧仓库的 `data/.config.yaml` 有未提交修改，不能通过 pull、reset 或部署覆盖。

这些事实证明旧目录确实包含不属于博物馆项目的业务数据和可触发逻辑。处理方式不是在旧目录内改名或清空表，而是把新项目部署到独立代码目录，挂载独立博物馆数据目录，并让运行时拒绝旧数据进入。

### 2.4 仍需确认的生产事实

1. 设备实际运行分区的应用 SHA，而不仅是版本字符串。
2. 某一次历史提醒对应的完整通知历史、投递回执和设备端日志。
3. 新部署目录、独立数据目录、端口和 HTTP/TLS 方案的用户确认。
4. 新服务部署后的真机 WebSocket、音频、屏幕和 OTA 表现。

## 3. 服务端残留清单

### 3.1 已提交版本仍可运行、但本地工作区已删除的旧业务

以下模块在 HEAD 和 origin/main 中仍存在并可被启动。本地工作区虽然已删除，但尚未形成提交和部署。

| 类别 | 主要路径 | 旧行为 | 清理决策 |
| --- | --- | --- | --- |
| 启动与总控 | main/xiaozhi-server/app.py、core/xiaoxin/control_runtime.py | 启动旧控制运行时、定时任务、Doorbell、Overview、陪伴 worker | 提交当前断开改动，然后删除旧运行时 |
| 课程与待办 | course_reminder_scheduler.py、todo_reminder_scheduler.py、identity/store.py | 学期、课表、课程提醒、待办提醒、补发与回执 | 删除 |
| 事件派发 | dispatcher.py、delivery_store.py、notification_history_store.py、control_types.py | xiaoxin_event、主动唤醒、可靠提醒 TTS、投递重试 | 删除旧业务派发；保留通用对话 TTS |
| 学生身份 | identity/*、activation_store.py、broker_auth.py | 学生、监护人、设备绑定、小程序账号、声纹主体解析 | 删除 |
| 声纹 | voiceprint_registration.py、core/utils/voiceprint_provider.py | 声纹注册和身份匹配 | 删除 |
| 陪伴与记忆 | companion/*、admin_memory.py、personal_pet_lifecycle.py | 关系成长、长期记忆、性格、情绪、学业阶段、宠物生命周期 | 删除 |
| 主动陪伴 | initiative_delivery.py、companion/initiative.py、companion/worker.py | 主动发起对话和长期调度 | 删除 |
| 合规与租户 | compliance/*、tenant_config.py | 旧学生、监护人和多租户能力控制 | 删除，除非新博物馆权限模型明确复用 |
| Overview | overview/*、network_observation.py | 天气、课程、待办、陪伴成长和通知总览 | 删除 |
| Doorbell | doorbell_client.py、doorbell_credentials.py、doorbell_ota.py | MQTT 反向唤醒、门铃凭据和配置下发 | 删除 |
| 旧控制接口 | core/api/xiaoxin_control_handler.py | 旧控制台、小程序、课程、待办、记忆和管理 API | 删除 |
| 旧插件 | plugins_func/functions/* | 天气、新闻、音乐、Home Assistant、RAG、搜索、角色切换、提醒 | 删除旧函数并改为显式允许列表 |
| 旧工具与验收 | scripts/xiaoxin_*、tools/companion_harness、tools/individuality_validation | 陪伴人格评估、旧真机门槛和旧发布检查 | 删除或移出当前项目 |
| 旧测试 | tests/xiaoxin/* | 为上述旧业务提供的大量测试 | 随旧业务删除 |

### 3.2 当前工作区仍实际存在的服务端残留

#### A. 当前可见行为

1. main/xiaozhi-server/agent-base-prompt.txt
   - 仍声明 You are Xiaoxin。
   - 虽然禁止学生陪伴、电子宠物、天气和提醒，但身份名称仍是旧项目。

2. main/xiaozhi-server/core/museum/answering.py
   - 问候、身份、能力、感谢和告别已经有正常对话路由。
   - 身份和问候回复仍硬编码“我是小芯”。

3. main/xiaozhi-server/core/api/static/xiaoxin_control.html
   - 当前 HTTP 路由已经不再暴露它。
   - 文件仍包含课程提醒、待办提醒和“高等数学”示例。
   - 属于孤立静态残留，应删除，不能仅依赖“暂时没有路由”。

#### B. 持久化数据

当前 data 目录仍有：

- xiaoxin_activation.db
- xiaoxin_companion.db、-shm、-wal
- xiaoxin_control.db
- xiaoxin_doorbell_credentials.db
- xiaoxin_notification_history.db
- xiaoxin_knowledge/campus_directory.json
- xiaoxin_knowledge/campus_life.json
- xiaoxin_knowledge/college_companion_facts.json
- xiaoxin_knowledge/student_affairs_qa.json

本地表当前均无业务记录，但 schema 完整存在。线上数据库不能据此推定为空。

这些数据库被 .gitignore 忽略，也被 .dockerignore 排除，因此不会进入新镜像；但 Compose 的 data 目录挂载会长期保留生产数据。

#### C. 失效脚本、部署文件和基础设施

1. docker-setup.sh
   - 会下载旧上游仓库的 Compose 文件。
   - 会操作 /opt/xiaozhi-server、xiaozhi-esp32-server 和旧镜像。
   - 在本项目中属于危险文件，应删除或完全重写。

2. Dockerfile-voiceprint-cpu
   - 仍构建旧 voiceprint-api。
   - 当前 Compose 和博物馆业务不使用。
   - 应删除。

3. Dockerfile-server-base 与 build-base-image.yml
   - CI 仍构建旧命名的 server-base。
   - 当前 Dockerfile-server 已经自包含，不使用该基础镜像。
   - 属于无效发布面，应删除或重新接入，不能两套并存。

4. main/xiaozhi-server/mosquitto
   - 当前 Compose 已删除 MQTT 服务，但配置目录仍在。
   - 应随 Doorbell 删除。

5. main/xiaozhi-server/scripts/xiaoxin_individuality_gate.py
   - 仍导入已被本地删除的 tools/individuality_validation。
   - 当前处于必然导入失败状态。
   - 应删除。

6. Python 缓存
   - core、scripts 等目录仍有被删除模块的 pyc。
   - 不进入 Git，但会干扰人工审计和本地导入判断。
   - 应清理生成物。

#### D. 配置和依赖表面

1. config.yaml 仍包含：
   - 天气和量子计算模块测试问题
   - 多个旧唤醒词
   - 新闻、Home Assistant、音乐、RAG、联网搜索配置
   - 大量当前未使用 provider 模板

2. mcp_server_settings.json 仍展示：
   - Home Assistant
   - 文件系统
   - Playwright
   - Windows CLI
   - 任意 SSE 和 streamable HTTP 服务

3. requirements.txt 仍包含与旧能力高度相关的依赖：
   - mem0ai
   - powermem
   - bs4
   - PyJWT
   - paho-mqtt
   - mcp-proxy

这些依赖不能按名称直接批量删除。应先固定比赛实际使用的 ASR、LLM、TTS、VAD、OTA 和测试工具，再通过导入检查逐项移除。

#### E. 调试工具与旧标签

`main/museum-web-test` 作为浏览器语音链路调试工具保留，不是比赛主产品。

处理原则：

- 已改名为中性的浏览器语音测试客户端 `museum-web-test`。
- 客户端标识已替换为 `museum-web-test`，默认唤醒词收敛为“你好讲解员”和“你好博物馆”。
- 禁止把它当作新的用户产品或后台。
- 如果后续真机测试已完全替代它，再单独删除；当前不把它当作比赛主链路。

## 4. 固件残留清单

### 4.1 已提交版本和远端版本仍包含的旧业务

固件 HEAD 与 origin/main 均为 2802b32。博物馆切换全部位于未提交工作区。

已提交版本仍编译或使用：

| 类别 | 主要路径 | 旧行为 | 清理决策 |
| --- | --- | --- | --- |
| Doorbell MQTT | main/doorbell_* | MQTT 配置、订阅、反向唤醒和遥测 | 删除 |
| 位置心跳 | device_location_heartbeat.* | 为旧主动通知链路上报位置和存活 | 删除或重新定义为博物馆设备遥测后重写 |
| xiaoxin_event | application.cc、xiaoxin_event_validation.h | 接收 notification、course_reminder、todo_reminder | 删除旧消息类型 |
| Overview | xiaoxin_overview_payload_contract.*、目标板 overview model | 天气、课程、待办、陪伴成长和通知卡片 | 删除 |
| 旧卡片分页 | xiaoxin_card_pager.* | 首页、Overview、通知中心和课程卡片 | 删除 |
| 电子宠物 | paopao_pet_* | 情绪、心情、行为、触发器和 GIF 状态 | 用博物馆页面替换后删除 |
| 旧 ACK | Protocol 和 Application 中的 xiaoxin_ack | 旧主动事件投递确认 | 删除；保留可靠 TTS ACK |

### 4.2 当前工作区仍在编译和运行的旧行为

#### A. 电子宠物仍是目标板主界面

当前 CMake 仍显式加入：

- paopao_pet_emotion.c
- paopao_pet_mood.c
- paopao_pet_behavior.c

目标板源码仍：

- 初始化 trigger、mood、behavior
- 根据聆听、思考、说话、失败、睡眠切换宠物状态
- 根据服务端 emotion 触发宠物反应
- 在低电量、网络和交互时更新宠物心情
- 使用宠物 GIF 作为主要视觉表现

本地 ai_pet.map 中仍有 paopao_pet_behavior_init 和 paopao_pet_mood_init 的链接地址。这不是死文件，而是当前活动逻辑。

#### B. museum_state 只解析，不显示

当前新增 MuseumState：

- 校验版本、必填字段、数值范围和枚举
- 生成 BuildMuseumStateDisplayText 文本

但 Application 收到 museum_state 后只执行：

- ParseMuseumState
- 记录 exhibit 和 status 日志

当前没有任何显示层调用 BuildMuseumStateDisplayText，也没有专用的 ApplyMuseumState、SetMuseumState 或 RenderMuseum 接口。

因此现有文档中“目标板能够显示博物馆知识状态”的表述高于真实实现。当前只能确认消息被解析，不能确认屏幕已应用。

#### C. 通用 notification 入口仍接受服务器任意文字

application.cc 仍接受 type=notification，并直接调用 ShowNotification。

此入口的注释仍描述 Doorbell 唤醒后的服务器通知。处理方式应为：

- 删除 Doorbell 专用语义。
- 如果博物馆确实需要系统通知，改为受版本和枚举约束的 museum_system_notice。
- Wi-Fi、低电量和 OTA 等本地系统提示继续走内部显示 API，不需要开放任意服务器文字入口。

#### D. Doorbell 唤醒 TTS 代码仍留在 Application

WakeForNotification、HandleNotificationWakeEvent、ContinueOpenNotificationChannel 和 NotificationTtsOrigin 仍在编译。

当前唯一明确调用者位于已经从 CMake 移除的 doorbell_mqtt.cc，因此这部分属于活动二进制中的无调用旧代码。应在确认无新业务调用后删除，但必须保留普通对话和可靠 TTS 的状态恢复能力。

#### E. 旧命名但可能仍有通用价值的板级模块

当前仍编译：

- xiaoxin_power_control
- xiaoxin_system_overlay
- xiaoxin_settings_model
- xiaoxin_low_power_clock_model

同目录还有 xiaoxin_battery_level 和 xiaoxin_battery_state。

这些模块主要承担电源、系统覆盖层、设置和电池等通用能力。处理原则是：

- 不按名称删除。
- 先确认真实调用和目标板行为。
- 保留功能，随后重命名为 museum_* 或 board_*。

### 4.3 当前工作区中的死文件、测试和文档

已确认的残留规模：

- paopao_pet 源文件：16 个
- xiaoxin 板级源文件：12 个
- Doorbell 源文件：10 个
- main/xiaoxin_* 根级旧源文件：4 个
- docs/xiaoxin* 文档：10 个
- docs/superpowers 旧计划和设计：31 个
- tests/xiaoxin* 测试：49 个
- tests/paopao_pet* 测试：5 个

分类处理：

1. 删除：
   - Doorbell
   - Overview
   - 课程和待办通知
   - 旧通知中心
   - 电子宠物业务及其专用测试和设计文档

2. 重命名并保留：
   - 电池
   - 电源控制
   - 系统覆盖层
   - 设置
   - 运行健康
   - OTA
   - 可靠 TTS

3. 暂时保留：
   - /xiaoxin/v1/ 和 /xiaoxin/ota/ 兼容路径
   - 通用 WebSocket、Opus、ASR、TTS、OTA、触摸、屏幕和系统告警基础设施

## 5. 清理决策矩阵

| 对象 | 决策 | 前置条件 |
| --- | --- | --- |
| 服务端 core/xiaoxin 旧业务 | 删除 | 当前本地断开改动通过最小测试并形成提交 |
| 课程、待办和提醒数据库 | 从活动数据目录移除 | 生产备份和提醒来源取证完成 |
| xiaoxin_control.html | 立即删除 | 无 |
| 小芯 prompt 和硬编码回复 | 替换为中性博物馆身份 | 确认正式产品名称；未确认时使用“金潮杯博物馆语音讲解助手” |
| docker-setup.sh | 删除或完全重写 | 无，当前脚本不得用于部署 |
| Dockerfile-voiceprint-cpu | 删除 | 确认无外部独立发布依赖 |
| Mosquitto 配置 | 删除 | Doorbell 不再使用 |
| 旧 MCP 模板 | 删除或收缩为明确允许列表 | 确认比赛不使用外部 MCP |
| 旧 provider 和依赖 | 分批删除 | 静态导入、启动和真机链路验证 |
| 固件 Doorbell、Overview、卡片分页 | 删除 | 当前工作区移除改动完成编译 |
| 固件电子宠物 | 替换后删除 | 博物馆页面能够显示 ready、retrieving、grounded、unsupported、missing_context |
| xiaoxin 电源、电池、设置模块 | 保留后重命名 | 功能测试通过；当前仅剩低风险技术命名 |
| museum-web-test | 保留为调试工具 | 已完成中性目录和默认唤醒词改造 |
| `/museum/v1/`、`/museum/ota/` 与旧 `/xiaoxin/*` 路径 | 正式路径与迁移兼容并存 | 新固件默认使用并只接受博物馆 WebSocket 路径；现场设备覆盖证据仍待补齐 |
| main/xiaozhi-server 目录名 | 暂留 | 低优先级结构重命名，不得与业务清理混在一起 |

## 6. 分阶段清理计划

### 阶段 0：生产取证与立即止血

目标：先阻止旧提醒继续发生，同时保留可追溯证据。

步骤：

1. 确认真实服务器目录、Compose 文件、容器、镜像和健康入口。
2. 记录本地 HEAD、GitHub origin/main、服务器 HEAD 和三方工作区状态。
3. 只读导出以下信息：
   - 生产配置中的旧运行时和调度开关，不输出密钥
   - student_courses、student_todos 和提醒设置行数
   - 命中设备的课程名称、开始时间、提前提醒时间和最后提醒时间
   - 提醒前后日志
4. 备份生产 data 目录和数据库。
5. 确认设备实际应用 SHA、运行分区和 OTA 请求内容。
6. 完成证据后执行止血：
   - 关闭旧课程和待办调度开关
   - 停止旧容器或部署已经断开旧运行时的服务端
   - 不把“清空本地数据库”当作止血

退出门槛：

- 生产进程中不存在旧 reminder loop。
- 生产日志不再出现 course_reminder 或 todo_reminder 派发。
- 旧控制接口不可达。
- 数据备份可恢复。

回滚：

- 保留原镜像 ID、原配置和数据库备份。
- 只允许恢复服务基础能力，不恢复旧课程和待办调度开关。

### 阶段 1：把现有未提交改造整理为可审查提交

目标：避免把 12 万行删除和多个行为修改压成一个不可审查提交。

建议提交顺序：

1. 服务端停止启动旧小芯运行时。
2. 服务端删除学生、陪伴、课程、待办、Doorbell、Overview 和旧控制 API。
3. 服务端清理旧配置、脚本、依赖和部署文件。
4. 服务端修正博物馆对话身份和寒暄行为。
5. 固件移除 xiaoxin_event、Doorbell、Overview 和旧 ACK。
6. 固件建立 museum_state 显示接口。
7. 固件用博物馆界面替换电子宠物。
8. 固件清理旧测试、文档和命名。

要求：

- 不回退用户已有未提交修改。
- 每个提交使用中文说明。
- 服务端和固件分别提交，不跨仓库伪造原子提交。
- 每个提交只解决一个可验证问题。

### 阶段 2：服务端业务和身份清理

步骤：

1. 删除孤立旧控制台。
2. 删除失效 xiaoxin_individuality_gate.py 和 pyc。
3. 将有效身份统一为中性博物馆讲解助手：
   - prompt
   - 本地寒暄回复
   - LLM 路由 prompt
   - 测试期望
4. 保留“你好、你是谁、你会什么、谢谢、再见”的正常响应。
5. 继续限制未经馆方资料支持的展品事实，不把事实约束误删成通用闲聊。
6. 将 config.yaml 收缩到实际使用模块。
7. 将 MCP 配置改为默认无服务，并删除示例中的高风险通用工具。
8. 静态扫描依赖后删除旧包。
9. 删除 Voiceprint、Mosquitto 和旧部署脚本。
10. 修正 CI 中旧 xiaozhi 命名和未使用的基础镜像工作流。

建议的三个聚焦自动化测试：

1. 服务启动后不会导入或创建 core.xiaoxin 运行时。
2. “你好，你是谁”返回中性博物馆身份，且不出现小芯、学生、宠物或提醒能力。
3. HTTP 路由中不存在旧控制台和小程序接口，博物馆 OTA 兼容路由仍可用。

### 阶段 3：生产数据目录切换

目标：不再让新博物馆服务复用旧项目的整块 data 目录。

步骤：

1. 创建新的博物馆数据目录。
2. 只迁移：
   - .config.yaml 中当前必要且不含旧业务的配置
   - museum_demo.db 或后续正式博物馆数据库
   - museum_firmware_releases.db
   - museum_firmware 固件制品目录
3. 不迁移任何 xiaoxin_*.db、旧知识 JSON、Doorbell 凭据或旧 WAL 文件。
4. 将旧 data 目录归档到 Compose 挂载范围之外。
5. 调整 Compose，避免继续挂载混合数据目录。
6. 增加启动检查：活动数据目录出现 xiaoxin_*.db 时给出明确错误或高优先级告警。

退出门槛：

- 新容器启动不创建任何旧项目数据库。
- 活动数据目录只包含博物馆配置、内容、审计和固件发布数据。
- 旧数据归档可恢复但不会被进程读取。

### 阶段 4：固件博物馆界面替换

目标：先建立真实可见的博物馆页面，再删除宠物主界面，避免产生空白或退化界面。

步骤：

1. 为 Display 增加明确接口，例如 ApplyMuseumState。
2. Application 在 ParseMuseumState 成功后：
   - 原子替换上一个完整状态
   - 调用显示接口
   - 非法状态保持上一状态
3. 目标板至少显示：
   - 当前展品
   - ready
   - retrieving
   - grounded 及来源数量
   - unsupported
   - missing_context
   - 当前观察任务
4. 保留 Wi-Fi、低电量、OTA 和故障覆盖层。
5. 将宠物 GIF、mood、behavior、trigger 和专用情绪反应从主链路移除。
6. 删除旧通知卡片、课程卡片、待办卡片和 Overview 页面。
7. 删除无调用的 Doorbell notification wake 代码。
8. 将通用 xiaoxin 电源、电池、设置和覆盖层模块改为中性命名。

建议的三个聚焦自动化测试：

1. MuseumState 合同解析和非法状态原子拒绝。
2. 五种博物馆状态映射到目标板可观察页面模型。
3. 固件构建 map 不再包含 paopao_pet、doorbell、xiaoxin_overview 或 xiaoxin_card_pager 符号。

真机验收：

- 用照片或视频确认屏幕，而不是仅看串口日志。
- 验证状态变化不会被聊天字幕或系统覆盖层永久遮挡。
- 验证触摸、低电量、断网和 OTA 提示仍正常。

### 阶段 5：协议兼容和命名迁移

目标：清理旧名字，但不通过一次性改路径使现场设备全部断连。

步骤：

1. 先保留现有 /xiaoxin/v1/ 和 /xiaoxin/ota/。
2. 增加新的 /museum/v1/ 和 /museum/ota/ 别名。
3. 发布新固件改用 museum 路径。
4. 通过 OTA 观察记录确认所有目标设备已经迁移。
5. 再决定是否移除 xiaoxin 兼容路径。
6. main/xiaozhi-server 目录重命名放到最后单独执行。

兼容路径必须在代码和文档中标记为“传输兼容”，不能继续承载旧学生陪伴语义。

### 阶段 6：唯一版本 OTA 发布和生产部署

步骤：

1. 固件版本必须从 0.1.4 升到新的唯一版本。
2. 构建后记录：
   - Git 提交
   - 版本
   - SHA-256
   - 目标板
   - 编译配置
3. 通过 publish_firmware.py 写入：
   - firmware_artifacts
   - firmware_releases
   - 目标设备或渠道允许规则
4. 先对一台设备灰度。
5. 设备升级后再次请求 OTA，应返回已是最新版本。
6. 核对设备上报的应用 SHA，而不是只核对版本字符串。
7. 服务端按既定部署规范执行：
   - 聚焦验证
   - 中文提交
   - push
   - 服务器快进拉取
   - 部署后健康和业务验收

退出门槛：

- release 和 artifact 不再为 0。
- 设备不再持续收到 no_eligible_release。
- 设备运行 SHA 与发布制品一致。
- 生产服务端和设备均不再接受旧课程、待办、Doorbell 和 Overview 业务消息。

### 阶段 7：文档、测试和仓库表面清理

步骤：

1. 删除旧陪伴、课程、待办、Doorbell、Overview 和宠物专用文档。
2. 对确有历史价值的材料移到明确的 archive/legacy 目录，并声明不可作为当前需求依据。
3. 删除旧业务测试。
4. 将电池、电源、设置、运行健康、TTS 和 OTA 测试改为中性名称。
5. 更新 README、CONTEXT、协议文档、部署文档和真机验收模板。
6. 将浏览器调试工具改为明确的开发工具名称和 `museum-web-test` 标识。
7. 清理 .gitignore 中失效的 xiaoxin 专用规则。

## 7. 最终验收标准

### 7.1 服务端

- 启动代码不导入 core.xiaoxin。
- 进程中不存在课程、待办、主动陪伴、Doorbell 或 Overview 定时任务。
- HTTP 路由中不存在旧控制台和小程序业务接口。
- 活动数据目录不会创建或读取 xiaoxin_*.db。
- prompt、固定回复、日志和用户可见文案中不再出现“小芯”旧身份。
- “你好，你是谁”能自然回答。
- 展品问题仍受已审核事实约束。

### 7.2 固件

- 构建 map 中不存在：
  - paopao_pet
  - doorbell
  - xiaoxin_overview
  - xiaoxin_card_pager
  - course_reminder
  - todo_reminder
- museum_state 被真正应用到屏幕。
- 设备冷启动、联网、聆听、思考、播报、失败、低电量和 OTA 均有博物馆语义下的可观察状态。
- 设备不处理旧 xiaoxin_event 课程和待办卡片。
- 可靠 TTS ACK 保持可用。

### 7.3 部署和数据

- 本地、GitHub、服务器三方提交一致。
- 生产容器镜像可追溯到中文 Git 提交。
- 生产数据目录没有旧业务数据库。
- 旧数据库有离线备份，但不在活动挂载中。
- 设备固件 SHA 与发布制品一致。
- 旧提醒时间窗口内不再出现任何课程或待办派发日志。

## 8. 禁止事项

1. 不得只替换 xiaoxin 字符串后宣称清理完成。
2. 不得直接删除所有含 xiaoxin 的协议和板级模块。
3. 不得用本地空数据库推断生产数据库为空。
4. 不得继续复用 0.1.4 版本号发布不同二进制。
5. 不得在没有博物馆页面替代品时先删除宠物显示代码。
6. 不得运行当前 docker-setup.sh 部署本项目。
7. 不得把服务启动成功表述为课程提醒、屏幕或真机验收通过。
8. 不得在部署信息未确认前沿用旧 /opt/xiaozhi-server 或旧容器名称。

## 9. 推荐执行顺序

严格顺序如下：

1. 生产只读取证。
2. 关闭旧调度并完成止血。
3. 整理并提交服务端旧运行时删除。
4. 清理服务端身份、配置、数据和部署面。
5. 实现固件 museum_state 真正显示。
6. 删除宠物、Doorbell、Overview 和旧通知代码。
7. 发布唯一版本 OTA 并灰度。
8. 完成生产部署和真机验收。
9. 最后处理兼容路径、目录名和历史文档。

不能把第 9 步的命名整洁放在第 1 至第 6 步的真实行为清理之前。
