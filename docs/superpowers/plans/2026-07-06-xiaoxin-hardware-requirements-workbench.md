# Xiaoxin Hardware Requirements Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `hardware_requirements` section to the Xiaoxin requirements workbench, with validation and HTML rendering beside the existing mini-program section.

**Architecture:** Keep `docs/requirements/requirements.yaml` as the single fact source. Add a reusable optional requirements-section validator in `docs/requirements/server.py`, then generalize the existing mini-program HTML renderer so both mini-program and hardware sections use one code path.

**Tech Stack:** Python 3.12, PyYAML, `http.server`, static HTML/CSS/JavaScript, pytest.

## Global Constraints

- Do not modify firmware code in this task.
- Do not copy the firmware repository feature map wholesale into this server repository.
- Do not split `requirements.yaml`; add one top-level `hardware_requirements` section.
- Track both product experience and engineering delivery for hardware columns.
- Hardware completion must not be inferred from server completion alone.
- `hardware_requirements` is optional for backward compatibility.

---

## File Structure

- Modify `docs/requirements/requirements.yaml`: add `hardware_requirements` after `mini_program_requirements`, with 8 `HW-*` columns and `related_items` links to existing `items`.
- Modify `docs/requirements/server.py`: add reusable validation for optional requirement sections and validate `hardware_requirements`.
- Modify `docs/requirements/requirements.html`: replace mini-program-only rendering with generic section rendering for `mini_program_requirements` and `hardware_requirements`.
- Create `docs/requirements/test_requirements_workbench.py`: focused pytest coverage for validation, YAML content, and static HTML rendering hooks.

---

### Task 1: Add Requirements Workbench Contract Tests

**Files:**
- Create: `docs/requirements/test_requirements_workbench.py`

**Interfaces:**
- Consumes: `server.load_requirements() -> dict[str, Any]`, `server.validate_requirements(data: Any) -> list[str]`
- Produces: Tests that later tasks must satisfy without changing their names.

- [ ] **Step 1: Write the failing test file**

Create `docs/requirements/test_requirements_workbench.py` with this complete content:

```python
from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
SERVER_PATH = ROOT / "server.py"
YAML_PATH = ROOT / "requirements.yaml"
HTML_PATH = ROOT / "requirements.html"


def load_server_module():
    spec = importlib.util.spec_from_file_location("requirements_server", SERVER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_yaml_data() -> dict[str, Any]:
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_current_requirements_yaml_passes_validation():
    server = load_server_module()
    payload = server.load_requirements()

    assert payload["ok"] is True
    assert payload["errors"] == []


def test_hardware_requirements_section_has_expected_columns_and_links():
    data = load_yaml_data()
    section = data["hardware_requirements"]
    item_ids = {item["id"] for item in data["items"]}

    assert section["title"] == "硬件端需求分栏"
    assert section["priority_order"] == [
        "HW-00 设备连接、OTA 与绑定闭环",
        "HW-01 语音播放、播报完成与 ACK",
        "HW-02 通知中心与 heads-up 提醒",
        "HW-03 Overview 总览页真实数据",
        "HW-04 宠物主页与轻量状态",
        "HW-05 本机设置、配网与设备信息",
        "HW-06 低功耗、电池与运行健康",
        "HW-07 真机发布、烧录、OTA 与长稳验收",
    ]

    columns = section["columns"]
    assert [column["id"] for column in columns] == [
        "HW-00",
        "HW-01",
        "HW-02",
        "HW-03",
        "HW-04",
        "HW-05",
        "HW-06",
        "HW-07",
    ]

    for column in columns:
        assert column["title"]
        assert column["focus"]
        assert column["implemented"]
        assert column["requirements"]
        assert column["remaining"]
        assert column["acceptance"]
        assert column["related_items"]
        assert all(item_id in item_ids for item_id in column["related_items"])


def test_optional_hardware_section_is_backward_compatible():
    server = load_server_module()
    data = load_yaml_data()
    data_without_hardware = deepcopy(data)
    data_without_hardware.pop("hardware_requirements", None)

    errors = server.validate_requirements(data_without_hardware)

    assert errors == []


def test_hardware_section_validation_reports_bad_status_and_unknown_related_item():
    server = load_server_module()
    data = load_yaml_data()
    broken = deepcopy(data)
    broken["hardware_requirements"]["columns"][0]["status"] = "almost"
    broken["hardware_requirements"]["columns"][0]["related_items"] = [
        "XIAOXIN-NOT-REAL"
    ]

    errors = server.validate_requirements(broken)

    assert (
        "hardware_requirements.HW-00: status='almost' 未在 taxonomy.statuses 中定义。"
        in errors
    )
    assert (
        "hardware_requirements.HW-00: related_items 引用了未知条目 'XIAOXIN-NOT-REAL'。"
        in errors
    )


def test_requirements_html_uses_generic_requirement_section_renderer():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function renderRequirementSection(" in html
    assert "renderRequirementSection(\"mini_program_requirements\"" in html
    assert "renderRequirementSection(\"hardware_requirements\"" in html
    assert "data-section-item" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest docs\requirements\test_requirements_workbench.py -q
```

Expected: FAIL. At minimum, `test_hardware_requirements_section_has_expected_columns_and_links` fails because `hardware_requirements` does not exist yet, and `test_requirements_html_uses_generic_requirement_section_renderer` fails because the renderer is mini-program-specific.

- [ ] **Step 3: Commit the failing contract tests**

Run:

```powershell
git add docs\requirements\test_requirements_workbench.py
git commit -m "test: cover hardware requirements workbench contract"
```

Expected: commit succeeds with only the new test file staged.

---

### Task 2: Add Hardware Section Data And Validator

**Files:**
- Modify: `docs/requirements/requirements.yaml`
- Modify: `docs/requirements/server.py`
- Test: `docs/requirements/test_requirements_workbench.py`

**Interfaces:**
- Consumes: Test expectations from Task 1.
- Produces: `validate_requirement_section(section_key: str, section: Any, taxonomy_sets: dict[str, set[str]], item_ids: set[str], errors: list[str]) -> None`

- [ ] **Step 1: Add the reusable validator to `server.py`**

In `docs/requirements/server.py`, after the existing `validate_items` function and before `validate_taxonomy_ref`, add:

```python
def validate_requirement_section(
    section_key: str,
    section: Any,
    taxonomy_sets: dict[str, set[str]],
    item_ids: set[str],
    errors: list[str],
) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        errors.append(f"{section_key} 必须是映射对象。")
        return

    columns = section.get("columns")
    if not isinstance(columns, list):
        errors.append(f"{section_key}.columns 必须是列表。")
        return

    statuses = taxonomy_sets.get("statuses", set())
    priorities = taxonomy_sets.get("priorities", set())
    seen_ids: set[str] = set()
    duplicates: set[str] = set()

    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            errors.append(f"{section_key}.columns[{index}] 必须是映射对象。")
            continue

        column_id = str(column.get("id") or f"columns[{index}]")
        label = f"{section_key}.{column_id}"

        for field in ("id", "title", "priority", "status"):
            if field not in column:
                errors.append(f"{label}: 缺少必填字段 {field}")

        if column_id in seen_ids:
            duplicates.add(column_id)
        seen_ids.add(column_id)

        status = column.get("status")
        if status not in statuses:
            errors.append(
                f"{label}: status={status!r} 未在 taxonomy.statuses 中定义。"
            )

        priority = column.get("priority")
        if priority not in priorities:
            errors.append(
                f"{label}: priority={priority!r} 未在 taxonomy.priorities 中定义。"
            )

        for field in (
            "implemented",
            "requirements",
            "remaining",
            "acceptance",
            "related_items",
        ):
            if field in column and not isinstance(column[field], list):
                errors.append(f"{label}: {field} 必须是列表。")

        for ref in column.get("related_items", []) or []:
            if str(ref) not in item_ids:
                errors.append(f"{label}: related_items 引用了未知条目 {ref!r}。")

    for column_id in sorted(duplicates):
        errors.append(f"{section_key}.columns 包含重复的 id：{column_id}")
```

In `validate_requirements`, after `validate_decisions(decisions, item_ids, errors)`, add:

```python
    validate_requirement_section(
        "mini_program_requirements",
        data.get("mini_program_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )
    validate_requirement_section(
        "hardware_requirements",
        data.get("hardware_requirements"),
        taxonomy_sets,
        item_ids,
        errors,
    )
```

- [ ] **Step 2: Add `hardware_requirements` to `requirements.yaml`**

Insert this block after the existing `mini_program_requirements` block and before `items:`:

```yaml
hardware_requirements:
  title: 硬件端需求分栏
  summary: >
    硬件端是小芯 AI Pet 的主要陪伴入口，负责可见状态、语音播放、触摸反馈、
    通知提醒、总览摘要、低功耗和真机稳定性。硬件需求不能只写 UI 页面，
    必须同时写清 OTA、WebSocket、MQTT、ACK、数据注入、播放完成、离线恢复和实机验收。
  current_state:
    - 已有 firmware 条目覆盖宠物主页、通知中心、Overview、本机设置、低功耗、电池和运行健康。
    - 服务端已具备投递、ACK、课程提醒、普通提醒、通知历史和 Overview 摘要的部分能力。
    - 当前缺口是硬件端没有独立分栏，硬件体验与工程验收混在服务端和产品条目里。
  recommendation: >
    硬件端优先级应围绕“真机能稳定表达服务端真实数据”排序：
    先保证连接、绑定、OTA、投递和 ACK，再做通知中心与 Overview 真实数据，
    然后补宠物长期状态、本机设置、低功耗和发布验收。
  priority_order:
    - HW-00 设备连接、OTA 与绑定闭环
    - HW-01 语音播放、播报完成与 ACK
    - HW-02 通知中心与 heads-up 提醒
    - HW-03 Overview 总览页真实数据
    - HW-04 宠物主页与轻量状态
    - HW-05 本机设置、配网与设备信息
    - HW-06 低功耗、电池与运行健康
    - HW-07 真机发布、烧录、OTA 与长稳验收
  columns:
    - id: HW-00
      title: 设备连接、OTA 与绑定闭环
      priority: P0
      status: partial
      focus: 设备从刷入固件、请求 Xiaoxin OTA、拿到 WebSocket 地址、获得激活码、绑定学生账号到稳定连接私有服务端的闭环。
      implemented:
        - 服务端和文档已定义 `/xiaoxin/ota/` 与 `/xiaoxin/v1/` 私有路径。
        - OTA activation 已有服务端会话和绑定码设计，固件端已有激活码展示与本地播报码能力。
        - 控制台和小程序侧均已有设备绑定接口的第一阶段能力。
      requirements:
        - 固件默认 OTA 地址必须指向 Xiaoxin 私有服务端，不再默认引用上游 `/xiaozhi/ota/`。
        - 设备请求 OTA 后必须拿到设备可访问的 WebSocket 地址、版本信息和未绑定时的激活信息。
        - 绑定使用稳定 `device_id`，绑定后小程序和控制台看到的是同一台设备。
        - 真机必须能通过 OTA 返回地址连接 `/xiaoxin/v1/` 并完成一次语音链路。
      remaining:
        - 仍需保存一次真实固件 OTA 升级验收记录，包含设备、版本、固件包、下载地址和结果。
        - 仍需保存一次真机 WebSocket 连接验收记录，证明 OTA 地址和设备连接地址一致。
        - 正式发布前仍需稳定 HTTPS/WSS 域名和反向代理方案，避免 IP 变化导致重烧录。
      acceptance:
        - 新固件默认不再请求上游 OTA 路径。
        - 未绑定设备能显示或播报激活码，绑定后能进入正常连接流程。
        - 设备能从私有 OTA 响应进入私有 WebSocket 并完成一次语音问答。
      related_items:
        - XIAOXIN-001
        - XIAOXIN-002
        - XIAOXIN-PROD-003
    - id: HW-01
      title: 语音播放、播报完成与 ACK
      priority: P0
      status: partial
      focus: 服务端 TTS 音频下发后，硬件端能播放、上报接收和完成状态，并让 delivery 时间线可信推进。
      implemented:
        - 固件端存在 `SendXiaoxinAck` 和 `xiaoxin_event` 处理路径。
        - 服务端已有 `xiaoxin_ack` 消息处理和 TTS 播放完成兜底逻辑。
        - 测试覆盖控制台投递、ACK 处理和 TTS 完成态回写。
      requirements:
        - 硬件收到 `xiaoxin_event` 后至少回传 `device_received`。
        - 开启播报的投递需要区分正在播报、播报完成、播报失败和中断。
        - 服务端可以用音频发送完成作为第一版兜底，但需求账本必须标明这不是硬件真实播放完成证明。
        - 投递历史必须能追踪 event id、sentence id、delivery id 和 ACK 状态。
      remaining:
        - 真机侧仍需验证普通通知、课程提醒和待办提醒的 ACK 时序。
        - 硬件真实播放完成事件仍需和服务端兜底完成态区分展示。
        - 播放失败、中断和用户打断时的状态回传仍需实机确认。
      acceptance:
        - 控制台发送一条带播报通知后，设备显示通知、播放音频，并使 delivery 进入可信完成或明确失败状态。
        - 关闭播报的通知不会错误等待 TTS 完成。
        - 投递失败时通知历史能显示失败原因，而不是静默消失。
      related_items:
        - XIAOXIN-006
        - XIAOXIN-PROD-008
    - id: HW-02
      title: 通知中心与 heads-up 提醒
      priority: P0
      status: partial
      focus: 硬件端把课程提醒、普通提醒、系统状态和失败提示作为真实通知呈现，而不是聊天记录或静态样板。
      implemented:
        - 固件通知中心已支持 upsert、移除、单条清理、全部清理和优先级排序。
        - 低电量、Wi-Fi 断开、配网中和语音识别失败已接入通知路径。
        - 上课提醒 helper 已能把课程记录转换为通知事件。
      requirements:
        - 通知中心必须支持课程提醒、普通提醒、设备状态、OTA 状态和投递失败提示。
        - 同一类状态通知重复到达时更新已有卡片，不堆积。
        - heads-up 显示必须有优先级和冷却策略，避免打断语音主链路。
        - 通知条目需要能对应服务端 delivery id 或本地系统事件 id。
      remaining:
        - `ttl_ms` 自动过期扫描或定时移除仍未完成。
        - OTA 检查、下载中和升级失败等真实流程仍未注入通知中心。
        - 课程提醒和普通提醒还需要更多真机端到端验收。
      acceptance:
        - 服务端触发课程提醒时，硬件能显示 heads-up 或通知卡片。
        - 状态恢复后，对应系统通知能被移除或标记恢复。
        - 通知历史能回看硬件通知结果。
      related_items:
        - XIAOXIN-008
        - XIAOXIN-PROD-009
        - XIAOXIN-PROD-015
    - id: HW-03
      title: Overview 总览页真实数据
      priority: P0
      status: partial
      focus: 硬件 Overview 显示天气、课程、待办、时间、网络、电量和设备状态，并消费服务端真实摘要。
      implemented:
        - 固件已有 Overview UI 和 `xiaoxin_overview_model`。
        - 时间、网络和电量已能由本机状态构建。
        - 服务端已有课程、待办和小程序 Overview 摘要的部分接口。
      requirements:
        - 服务端真实数据必须能刷新硬件 Overview，而不是只存在于小程序或 HTTP API。
        - 今日课程、下一节课、未完成提醒数量和最近提醒摘要必须来自学生账号下的数据源。
        - 离线、未绑定、无课程、无提醒时必须显示明确空态。
        - 小圆屏只展示高价值摘要，避免堆太多文字。
      remaining:
        - 板级 `BuildOverviewState()` 仍主要填时间、网络和电量。
        - 天气摘要仍缺真实服务端同步源。
        - 硬件端真实拉取或接收 Overview 更新链路仍需真机验证。
      acceptance:
        - 小程序保存课程后，硬件 Overview 能显示今日课程或下一节课摘要。
        - 小程序创建待办后，硬件 Overview 能显示待提醒摘要。
        - 未绑定设备不显示假课程或假待办。
      related_items:
        - XIAOXIN-009
        - XIAOXIN-012
        - XIAOXIN-PROD-005
        - XIAOXIN-PROD-006
    - id: HW-04
      title: 宠物主页与轻量状态
      priority: P1
      status: partial
      focus: 宠物主页表达服务端情绪、本地触摸、摇晃、低电量、Wi-Fi 异常和轻量陪伴状态。
      implemented:
        - 固件已有泡泡宠物 GIF 主页、资源和渲染模块。
        - `paopao_pet_mood` 已将服务端和本地事件收束为宠物表现建议。
        - 本地测试覆盖情绪、行为、触发和集成路径。
      requirements:
        - 服务端 `llm.emotion` 必须能映射到核心宠物表现。
        - 触摸、摇晃、低电量、Wi-Fi 和语音错误不能造成动画频繁抖动。
        - 长期能量、心情或亲密度第一版可以轻量，但必须有本地或服务端事实来源。
        - 宠物短文案和提示不能遮挡核心通知和语音状态。
      remaining:
        - 长期能量、心情、亲密度和连续性格状态尚未完整实现。
        - 低功耗、充电和长时间无互动的宠物表现策略仍需补齐。
        - 宠物短文案和轻量提示输出通道尚未形成。
      acceptance:
        - 一次服务端情绪变化能在宠物主页产生稳定表现。
        - 触摸或摇晃能触发本地反馈，并且不会抢占 P0 通知。
        - 离线时宠物主页有明确本地状态，不伪装成已连接服务端。
      related_items:
        - XIAOXIN-007
        - XIAOXIN-PROD-004
        - XIAOXIN-PROD-011
        - XIAOXIN-PROD-012
    - id: HW-05
      title: 本机设置、配网与设备信息
      priority: P1
      status: partial
      focus: 硬件端提供 BOOT 长按设置入口、亮度、Wi-Fi 重新配网、关于页、省电开关和关键设备信息。
      implemented:
        - BOOT 长按已作为设置页主入口。
        - 设置页以 overlay 形式挂载，不加入三页分页状态机。
        - 亮度页已升级为动态滑条，支持预览和持久化。
        - Wi-Fi 页复用重新配网入口，关于页和省电页已落地。
      requirements:
        - 亮度、配网、省电开关和设备信息必须能在无小程序时本机操作。
        - 设置修改必须持久化，重启后保持。
        - 配网状态必须区分未连接、连接中、已连接和失败。
        - 设备信息至少应包含设备 ID、固件版本、OTA 地址或服务端连接摘要。
      remaining:
        - 音量、静音、提示音、震动仍未接入真实音频或震动配置。
        - 宠物主题、动画风格和睡眠时间等产品化配置尚未落地。
        - 省电相关实机体验仍需 build 和 flash 后确认。
      acceptance:
        - BOOT 长按稳定打开设置页。
        - 亮度修改重启后保持。
        - 重新配网路径不会误显示已连接。
      related_items:
        - XIAOXIN-010
    - id: HW-06
      title: 低功耗、电池与运行健康
      priority: P1
      status: partial
      focus: 硬件端管理电量、充电、低电、自动息屏、低功耗时钟、唤醒、重启原因和运行健康。
      implemented:
        - 目标板已接入省电调度器和低功耗时钟页。
        - POWER、BOOT 和触摸活动已接入唤醒路径。
        - 电量状态、低电告警、低电关机和运行健康测试已有覆盖。
        - SNTP 已扩展为多服务器并增加同步回调。
      requirements:
        - 设备可进入低功耗时钟，并通过 POWER、BOOT 或触摸唤醒。
        - 低电关键路径不能调用 `esp_restart()` 代替关机。
        - 低电量应有降亮度、提示和关机前状态表达。
        - 运行健康应记录最近重启原因、上次运行线索和关键同步状态。
      remaining:
        - 低电量自动降亮度、充电动画和电量曲线校准仍未落地。
        - 低功耗页日期、电量和同步状态等 AOD 信息仍需完善。
        - 自动息屏、唤醒和低功耗时钟需要真机长时间验证。
      acceptance:
        - 真机能进入低功耗时钟并从指定输入唤醒。
        - 低电状态有通知或宠物表现，不突然重启。
        - 长时间运行记录能支持定位异常重启或掉线。
      related_items:
        - XIAOXIN-011
        - XIAOXIN-015
    - id: HW-07
      title: 真机发布、烧录、OTA 与长稳验收
      priority: P0
      status: active
      focus: 把硬件端完成标准从“代码和模型测试存在”推进到“可构建、可烧录、可 OTA、可长时间运行”的证据链。
      implemented:
        - 文档已有 OTA 响应检查和固件版本发布检查清单。
        - 需求工作台已有固件仓库路径和证据引用。
        - 低功耗、通知、Overview 和设置已有多项模型或路径测试证据。
      requirements:
        - 每次准备发布新固件前必须记录固件版本、构建时间、包路径、包大小和 OTA 下载地址。
        - 至少保存一次 build、flash、OTA 升级和 WebSocket 连接的真机验收记录。
        - 长稳验收必须覆盖在线、睡眠、唤醒、低电或充电、断网恢复中的关键场景。
        - 硬件条目从 `partial` 升为 `done` 时，必须能指向真机记录或说明该项只需模型测试的理由。
      remaining:
        - 当前服务端仓库尚未保存完整真机发布验收记录。
        - ESP-IDF 完整 build、flash 验证需要在固件环境补跑。
        - 长时间运行、断网恢复和 OTA 升级后的回归记录仍需补齐。
      acceptance:
        - 需求工作台能明确标出哪些硬件能力已有真机证据，哪些只有模型测试。
        - 一次固件发布可以从需求工作台追溯到版本、包、设备和结果。
        - 失败的 OTA、连接或长稳验收不会被误标为完成。
      related_items:
        - XIAOXIN-002
        - XIAOXIN-011
        - XIAOXIN-013
        - XIAOXIN-015
```

- [ ] **Step 3: Run tests for YAML and validation**

Run:

```powershell
python -m pytest docs\requirements\test_requirements_workbench.py -q
```

Expected: tests related to YAML content and server validation PASS. The HTML renderer test still FAILS because `requirements.html` has not been generalized yet.

- [ ] **Step 4: Commit YAML and validator changes**

Run:

```powershell
git add docs\requirements\requirements.yaml docs\requirements\server.py
git commit -m "feat: add hardware requirements section validation"
```

Expected: commit succeeds with only YAML and validator changes staged.

---

### Task 3: Generalize HTML Requirements Section Rendering

**Files:**
- Modify: `docs/requirements/requirements.html`
- Test: `docs/requirements/test_requirements_workbench.py`

**Interfaces:**
- Consumes: `state.data.mini_program_requirements`, `state.data.hardware_requirements`
- Produces: `renderRequirementSection(sectionKey, options) -> string`, `renderRequirementDetail(column, emptyText) -> string`, `sectionRelatedList(values) -> string`

- [ ] **Step 1: Replace mini-program-only renderer functions**

In `docs/requirements/requirements.html`, replace the full bodies of `renderMiniProgramRequirements`, `renderMiniProgramDetail`, and `miniRelatedList` with these generic functions:

```javascript
      function renderRequirementSection(sectionKey, options) {
        const section = state.data[sectionKey];
        if (!section || !Array.isArray(section.columns) || !section.columns.length) {
          return "";
        }

        state.selectedRequirementSections ||= {};

        const columns = section.columns;
        const selectedId = state.selectedRequirementSections[sectionKey];
        const selected =
          columns.find((column) => column.id === selectedId) || columns[0];
        state.selectedRequirementSections[sectionKey] = selected.id;
        const currentState = renderList(section.current_state);
        const navItems = columns
          .map((column) => {
            const active = column.id === selected.id ? "active" : "";
            return `
              <li>
                <button class="module-button ${active}" data-section-item="${escapeHtml(sectionKey)}:${escapeHtml(column.id)}">
                  <span>${escapeHtml(column.title)}</span>
                  <span class="module-count">${escapeHtml(column.id)}</span>
                </button>
              </li>
            `;
          })
          .join("");

        const rows = columns
          .map((column) => {
            const active = column.id === selected.id ? "active" : "";
            const relatedItems = (column.related_items || [])
              .map((id) => `<span class="badge status-optimize">${escapeHtml(id)}</span>`)
              .join("");
            return `
              <tr class="${active}" data-section-item="${escapeHtml(sectionKey)}:${escapeHtml(column.id)}">
                <td class="id">${escapeHtml(column.id)}</td>
                <td class="title-cell">
                  <div class="title-main">${escapeHtml(column.title)}</div>
                  <div class="summary">${escapeHtml(column.focus)}</div>
                </td>
                <td>
                  <div class="badge-row">
                    <span class="badge ${statusClass(column.status)}">${escapeHtml(taxonomyLabel("statuses", column.status))}</span>
                    <span class="badge ${priorityClass(column.priority)}">${escapeHtml(column.priority)}</span>
                  </div>
                </td>
                <td><div class="miniprogram-links">${relatedItems}</div></td>
              </tr>
            `;
          })
          .join("");

        return `
          <section class="panel miniprogram-panel">
            <div class="panel-header">${escapeHtml(section.title || options.defaultTitle)}</div>
            <div class="miniprogram-overview">
              <div>
                <div class="miniprogram-summary">
                  <div class="miniprogram-label">判断</div>
                  ${escapeHtml(section.summary)}
                </div>
                <div class="miniprogram-recommendation">
                  <div class="miniprogram-label">下一步推荐</div>
                  ${escapeHtml(section.recommendation)}
                </div>
              </div>
              <div class="miniprogram-state">
                <div class="miniprogram-label">当前状态</div>
                <ul>${currentState}</ul>
              </div>
            </div>
            <div class="miniprogram-workspace">
              <aside class="miniprogram-nav">
                <ul class="miniprogram-nav-list">
                  <li class="module-group">${escapeHtml(options.groupLabel)}</li>
                  <li>
                    <button class="module-button" data-section-item="${escapeHtml(sectionKey)}:${escapeHtml(columns[0].id)}">
                      <span>${escapeHtml(options.allLabel)}</span>
                      <span class="module-count">${columns.length}</span>
                    </button>
                  </li>
                  ${navItems}
                </ul>
              </aside>
              <div class="miniprogram-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>条目</th>
                      <th>状态</th>
                      <th>关联条目</th>
                    </tr>
                  </thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
              ${renderRequirementDetail(selected, options.emptyText)}
            </div>
          </section>
        `;
      }

      function renderRequirementDetail(column, emptyText) {
        if (!column) {
          return `<aside class="miniprogram-detail"><div class="empty">${escapeHtml(emptyText)}</div></aside>`;
        }
        return `
          <aside class="miniprogram-detail">
            <div class="panel-header">需求详情</div>
            <div class="detail-body">
              <div class="badge-row">
                <span class="badge ${statusClass(column.status)}">${escapeHtml(taxonomyLabel("statuses", column.status))}</span>
                <span class="badge ${priorityClass(column.priority)}">${escapeHtml(column.priority)}</span>
              </div>
              <h2>${escapeHtml(column.id)} · ${escapeHtml(column.title)}</h2>
              <div class="summary">${escapeHtml(column.focus)}</div>
              ${detailList("已实现", column.implemented)}
              ${detailList("需求", column.requirements)}
              ${detailList("还需要做", column.remaining)}
              ${detailList("验收标准", column.acceptance)}
              ${sectionRelatedList(column.related_items)}
            </div>
          </aside>
        `;
      }

      function sectionRelatedList(values) {
        const list = values || [];
        if (!list.length) {
          return `
            <section class="detail-section">
              <h3>关联条目</h3>
              <div class="summary">暂无。</div>
            </section>
          `;
        }
        const badges = list
          .map((id) => `<span class="badge status-optimize">${escapeHtml(id)}</span>`)
          .join("");
        return `
          <section class="detail-section">
            <h3>关联条目</h3>
            <div class="miniprogram-links">${badges}</div>
          </section>
        `;
      }
```

- [ ] **Step 2: Update the render call sites**

In the `render()` template, replace:

```javascript
          ${renderMiniProgramRequirements()}
```

with:

```javascript
          ${renderRequirementSection("mini_program_requirements", {
            defaultTitle: "学生侧小程序需求",
            groupLabel: "学生侧小程序",
            allLabel: "全部小程序需求",
            emptyText: "选择一个小程序需求查看详情。",
          })}
          ${renderRequirementSection("hardware_requirements", {
            defaultTitle: "硬件端需求",
            groupLabel: "硬件端",
            allLabel: "全部硬件需求",
            emptyText: "选择一个硬件需求查看详情。",
          })}
```

- [ ] **Step 3: Update click handlers**

In `attachHandlers()`, replace the `[data-mini-item]` handler:

```javascript
        document.querySelectorAll("[data-mini-item]").forEach((row) => {
          row.addEventListener("click", (event) => {
            state.selectedMiniId = event.currentTarget.dataset.miniItem;
            render();
          });
        });
```

with:

```javascript
        document.querySelectorAll("[data-section-item]").forEach((row) => {
          row.addEventListener("click", (event) => {
            const value = event.currentTarget.dataset.sectionItem || "";
            const separator = value.indexOf(":");
            if (separator < 1) {
              return;
            }
            const sectionKey = value.slice(0, separator);
            const itemId = value.slice(separator + 1);
            state.selectedRequirementSections ||= {};
            state.selectedRequirementSections[sectionKey] = itemId;
            render();
          });
        });
```

- [ ] **Step 4: Update stats label**

In `renderStats(items)`, replace:

```javascript
        const miniprogramItems = state.data.mini_program_requirements?.columns?.length || 0;
```

with:

```javascript
        const miniprogramItems = state.data.mini_program_requirements?.columns?.length || 0;
        const hardwareItems = state.data.hardware_requirements?.columns?.length || 0;
```

Replace the `stat("风险", risks)` line in the stats template with:

```javascript
            ${stat("硬件条目", hardwareItems)}
            ${stat("风险", risks)}
```

This intentionally increases the stats count by one. The CSS grid already uses flexible tracks and can wrap on smaller screens.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest docs\requirements\test_requirements_workbench.py -q
```

Expected: PASS for all tests in `docs/requirements/test_requirements_workbench.py`.

- [ ] **Step 6: Commit HTML renderer changes**

Run:

```powershell
git add docs\requirements\requirements.html
git commit -m "feat: render hardware requirements section"
```

Expected: commit succeeds with only HTML changes staged.

---

### Task 4: Final Workbench Verification

**Files:**
- Modify: `docs/requirements/requirements.yaml` only if verification finds a content typo.
- Modify: `docs/requirements/requirements.html` only if verification finds a rendering typo.
- Modify: `docs/requirements/server.py` only if verification finds a validator typo.
- Test: `docs/requirements/test_requirements_workbench.py`

**Interfaces:**
- Consumes: Completed YAML section, validator, and renderer from Tasks 2 and 3.
- Produces: Verified workbench state and a final commit if any verification fixes are needed.

- [ ] **Step 1: Run focused workbench tests**

Run:

```powershell
python -m pytest docs\requirements\test_requirements_workbench.py -q
```

Expected: PASS with all tests passing.

- [ ] **Step 2: Run Xiaoxin server tests as regression coverage**

Run:

```powershell
python -m pytest main\xiaozhi-server\tests\xiaoxin -q
```

Expected: PASS. These tests should not be affected by requirements workbench changes.

- [ ] **Step 3: Verify JSON load from the workbench server module**

Run:

```powershell
@'
import importlib.util
from pathlib import Path

server_path = Path("docs/requirements/server.py")
spec = importlib.util.spec_from_file_location("requirements_server", server_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
payload = module.load_requirements()
print(payload["ok"])
print(len(payload["data"]["hardware_requirements"]["columns"]))
print(payload["data"]["hardware_requirements"]["columns"][0]["id"])
'@ | python -
```

Expected output:

```text
True
8
HW-00
```

- [ ] **Step 4: Verify HTML contains both rendered section calls**

Run:

```powershell
Select-String -Path docs\requirements\requirements.html -Pattern 'renderRequirementSection\("mini_program_requirements"|renderRequirementSection\("hardware_requirements"|data-section-item'
```

Expected: output includes matches for `mini_program_requirements`, `hardware_requirements`, and `data-section-item`.

- [ ] **Step 5: Inspect git status**

Run:

```powershell
git status --short
```

Expected: no unstaged changes if verification did not require fixes.

- [ ] **Step 6: Commit verification fixes only if files changed**

If `git status --short` shows changes from verification fixes, run:

```powershell
git add docs\requirements\requirements.yaml docs\requirements\requirements.html docs\requirements\server.py docs\requirements\test_requirements_workbench.py
git commit -m "fix: complete hardware requirements workbench verification"
```

Expected: commit succeeds only when there are actual verification fixes. If there are no changes, skip this step.

---

## Self-Review

Spec coverage:

- The plan adds `hardware_requirements` with 8 HW columns.
- The plan keeps `requirements.yaml` as one file.
- The plan validates required column fields, taxonomy status, taxonomy priority, list fields, duplicate IDs, and unknown `related_items`.
- The plan preserves backward compatibility when `hardware_requirements` is absent.
- The plan generalizes HTML rendering instead of adding a second copy of mini-program rendering.
- The plan verifies JSON load and static HTML hooks.

Placeholder scan:

- The plan contains no deferred fields, unresolved names, or incomplete code blocks.

Type consistency:

- `validate_requirement_section` is defined before use and uses the same signature in every reference.
- `renderRequirementSection`, `renderRequirementDetail`, and `sectionRelatedList` are defined before `render()` calls them.
- `state.selectedRequirementSections` is initialized lazily in both render and click paths.
