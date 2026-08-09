# 小芯发布前准备工作流

本文档是正式给真实用户使用前的发布门禁入口。它不替代现有 HIL harness，而是把发布前最容易漏掉的三件事放到一个顺序里：

1. 发布事实核对：本地、GitHub、服务器、Compose、健康入口和模型配置。
2. 上线前清库：清掉测试用户、测试设备绑定、测试 Evidence、测试 pet 和测试上下文。
3. 双设备长稳：两台已绑定真实设备连续运行并保留可复核证据。

## 工具入口

脚本路径：

    cd D:\AI_Pet\xiaoxin-esp32-server\main\xiaozhi-server
    .\.venv\Scripts\python.exe scripts\xiaoxin_release_readiness.py --help

如果在仓库根目录运行，使用：

    .\.venv\Scripts\python.exe main\xiaozhi-server\scripts\xiaoxin_release_readiness.py --help

## 1. 发布前只读检查

本地 dry-run：

    .\.venv\Scripts\python.exe main\xiaozhi-server\scripts\xiaoxin_release_readiness.py preflight ^
      --repo-root . ^
      --server-root main\xiaozhi-server ^
      --identity-db main\xiaozhi-server\data\xiaoxin_control.db ^
      --companion-db main\xiaozhi-server\data\xiaoxin_companion.db ^
      --health-url http://127.0.0.1:8003/health ^
      --output .codex_tmp\xiaoxin-release-preflight.json

必须人工确认：

- git.head、真实 GitHub origin/main 和服务器 HEAD 一致；脚本只能读取本地 origin/main，发布前仍必须执行真实远端刷新。
- dirty_paths_excluding_playwright_cli 为空；.playwright-cli/ 可忽略，但 tracked 文件改动不能忽略。
- llm.selected_module.LLM 和实际前台模型符合本次发布预期，例如 AliLLM / qwen3.7-flash。
- identity_db 与 companion_db 存在，关键表能被读取。
- Docker Compose 状态、业务日志和 HTTP 健康入口不能互相矛盾。

阻塞发布的典型情况：

- 本地还有未提交 tracked 改动。
- 本地 HEAD 与刷新后的 origin/main 或服务器 HEAD 不一致。
- 数据库缺失、健康入口失败、容器启动但业务接口不可用。
- 前台模型、semantic mode、worker、initiative delivery 等开关与验收记录不一致。

## 2. 上线前清库

默认只生成清库计划，不删除数据：

    .\.venv\Scripts\python.exe main\xiaozhi-server\scripts\xiaoxin_release_readiness.py clear-test-data ^
      --identity-db main\xiaozhi-server\data\xiaoxin_control.db ^
      --companion-db main\xiaozhi-server\data\xiaoxin_companion.db ^
      --output .codex_tmp\xiaoxin-clear-test-data-plan.json

确认输出后再执行。当前项目尚未真实上线，可不传 --backup-dir；真实上线后必须先备份再清理。

    .\.venv\Scripts\python.exe main\xiaozhi-server\scripts\xiaoxin_release_readiness.py clear-test-data ^
      --identity-db main\xiaozhi-server\data\xiaoxin_control.db ^
      --companion-db main\xiaozhi-server\data\xiaoxin_companion.db ^
      --execute ^
      --confirm CLEAR_XIAOXIN_TEST_DATA ^
      --output .codex_tmp\xiaoxin-clear-test-data-executed.json

带备份执行：

    .\.venv\Scripts\python.exe main\xiaozhi-server\scripts\xiaoxin_release_readiness.py clear-test-data ^
      --identity-db main\xiaozhi-server\data\xiaoxin_control.db ^
      --companion-db main\xiaozhi-server\data\xiaoxin_companion.db ^
      --backup-dir main\xiaozhi-server\data\backups ^
      --execute ^
      --confirm CLEAR_XIAOXIN_TEST_DATA ^
      --output .codex_tmp\xiaoxin-clear-test-data-executed.json

清理范围：

- xiaoxin_control.db：用户、session、设备绑定、speaker profile、memory subject、personal pet、学生资料、课表、待办和 admin audit。
- xiaoxin_companion.db：pet、relationship epoch、turn、Evidence、FTS 派生、召回审计、VA、interaction contract、observation、context、semantic evaluation、capsule、adjustment、chapter、growth moment、memory control、worker job、initiative opportunity/decision。

不会清理：

- Git 仓库文件。
- 服务器认证文件。
- 固件发布制品。
- Docker 镜像。
- .playwright-cli/ 和 .codex_tmp/。

## 3. 双设备 24 小时长稳计划

生成计划模板：

    .\.venv\Scripts\python.exe main\xiaozhi-server\scripts\xiaoxin_release_readiness.py longrun-plan ^
      --base-url http://127.0.0.1:8003 ^
      --duration-hours 24 ^
      --device-a-id "1c:db:d4:48:d1:50" ^
      --device-a-speaker-profile-id "spk_Smbr17SyOLJx6MCRDG6dq96" ^
      --device-a-memory-subject-id "ms_10j9s2kszy7z8sUoXX8WW27V" ^
      --device-a-pet-id "pet_If3AV5MgzWVmfzGCcd8NIKlh" ^
      --device-b-id "a0:f2:62:e3:91:d8" ^
      --device-b-speaker-profile-id "spk_blO6zPHWmq7IGA8n5rTGYa5t" ^
      --device-b-memory-subject-id "ms_YvavKuO4OClkRDSYgrR68EN" ^
      --device-b-pet-id "pet_KWMbiJc3kNvcMqPVyvUMQHZv" ^
      --output .codex_tmp\xiaoxin-dual-device-longrun-plan.json

每轮对话必须继续走文字输入、真机输出链路：

    POST /api/xiaoxin/devices/{device_id}/text-chat

每轮至少记录：

- 设备身份、speaker profile、memory subject、pet_id。
- 提交时间、输入原文、服务端回复原文。
- TTS 终态序列，例如 ready -> done -> speaking -> idle。
- 串口状态、人工听感、屏幕/表情观察。

硬失败：

- A 召回 B 的私人事实，或 B 召回 A 的私人事实。
- 交换 speaker profile 或只按 COM 口推断设备身份。
- candidate、旧 epoch、已删除或已遗忘内容被当成事实。
- 重连后重复播报、重复主动陪伴或跨用户投递。
- 设备重绑导致旧用户记忆或主动投递进入新用户。

## 发布前最小结论格式

发布给真实用户前，台账结论必须写成下面这种格式：

    Commit:
    GitHub origin/main:
    Server HEAD:
    Frontend LLM:
    Semantic mode:
    Worker / initiative delivery:
    Cleared test data: yes/no, report path
    Preflight report:
    Dual-device longrun report:
    Known blockers:
    Release decision: PASS / FAIL / INCONCLUSIVE

如果任一项是 FAIL 或 INCONCLUSIVE，不能扩大到真实新生长期使用。
