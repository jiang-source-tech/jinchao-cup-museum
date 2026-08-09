# 小芯项目状态工作台实施计划

本文是 2026-07-04 的历史实施计划归档。原计划目标是建立一个由 YAML 驱动、HTML 渲染、本地 Python 服务校验的小芯项目状态工作台。

## 目标

在 `D:\AI_Pet\xiaoxin-esp32-server\docs\requirements\` 下建立总状态工作台，用来记录已经实现、还需要实现、可以优化、存在风险和证据来源。

## 架构

- `requirements.yaml` 是唯一事实源。
- `server.py` 负责读取、校验并以 JSON 暴露 YAML 内容。
- `requirements.html` 负责浏览器端渲染、筛选和详情展示。
- 不依赖数据库、账号系统、外部网络或在线协作服务。

## 实施任务

1. 创建 `docs/requirements/requirements.yaml`，包含 `meta`、`taxonomy`、`repositories`、`modules`、`milestones`、`items`、`risks` 和 `decisions`。
2. 创建 `server.py`，提供 `/requirements.json`、`/requirements.yaml` 和 HTML 页面路由。
3. 创建 `requirements.html`，展示统计、筛选、状态矩阵、详情、里程碑、风险和决策。
4. 通过本地服务做端到端验证，确保 YAML 能解析、JSON 能返回、页面能渲染。

## 验收

- YAML 至少覆盖 12 个核心项目条目。
- 页面能清楚区分“已实现”“还需要做”“可优化”“风险”和“证据”。
- 本地启动后浏览器能访问 `http://127.0.0.1:8080`。
- 修改 YAML 后刷新页面即可看到新状态。

## 后续维护

工作台应跟随服务端、固件和产品路线持续更新。不能把它当成一次性展示页，否则跨仓库状态会再次分裂。
