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


def test_requirements_ledger_matches_current_cross_repo_product_facts():
    data = load_yaml_data()
    items = {item["id"]: item for item in data["items"]}
    risks = {risk["id"]: risk for risk in data["risks"]}
    decisions = {decision["id"]: decision for decision in data["decisions"]}

    assert data["meta"]["version"] == "0.17.0"
    assert str(data["meta"]["updated"]) == "2026-08-03"

    assert items["XIAOXIN-016"]["status"] == "partial"
    assert "受控 OTA" in items["XIAOXIN-016"]["title"]
    assert items["XIAOXIN-PROD-011"]["status"] == "partial"
    assert items["XIAOXIN-PROD-013"]["status"] == "partial"
    assert items["XIAOXIN-014"]["status"] == "active"
    assert items["XIAOXIN-017"]["status"] == "done"
    assert "turn_analysis.py" in "\n".join(items["XIAOXIN-017"]["implemented"])
    assert items["XIAOXIN-019"]["status"] == "done"
    assert items["XIAOXIN-020"]["status"] == "partial"
    assert items["XIAOXIN-PROD-019"]["priority"] == "P0"
    assert items["XIAOXIN-PROD-019"]["status"] == "partial"
    assert items["XIAOXIN-PROD-022"]["status"] == "partial"
    assert items["XIAOXIN-PROD-023"]["priority"] == "P0"
    assert items["XIAOXIN-PROD-023"]["status"] == "active"
    assert "大一新生陪伴型电子宠物" in items["XIAOXIN-PROD-001"]["title"]
    assert "personal_pets" in "\n".join(items["XIAOXIN-020"]["implemented"])
    assert "当前不具备学校账号绑定或学生身份认证" in items["XIAOXIN-PROD-002"]["summary"]

    hardware_state = "\n".join(data["hardware_requirements"]["current_state"])
    assert "device/{device_id}/telemetry" in hardware_state
    assert "高德" in hardware_state

    mqtt_risk = risks["RISK-005"]
    assert "device/{device_id}/telemetry" in mqtt_risk["summary"]
    assert "telemetry" in "\n".join(mqtt_risk["mitigation"])
    assert risks["RISK-006"]["severity"] == "high"
    assert "密钥" in risks["RISK-006"]["title"]

    mqtt_decision = decisions["DEC-004"]
    assert "device/{device_id}/telemetry" in mqtt_decision["decision"]

    prod_003_remaining = "\n".join(items["XIAOXIN-PROD-003"]["remaining"])
    assert "当前没有上报时返回空值" not in prod_003_remaining

    prod_006_remaining = "\n".join(items["XIAOXIN-PROD-006"]["remaining"])
    assert "接入真实 todo API" not in prod_006_remaining
    assert "编辑" in prod_006_remaining

    mp05_remaining = "\n".join(
        next(
            column["remaining"]
            for column in data["mini_program_requirements"]["columns"]
            if column["id"] == "MP-05"
        )
    )
    assert "接入通知历史接口" not in mp05_remaining


def test_integrated_companion_requirements_section_is_the_current_master_view():
    data = load_yaml_data()
    section = data["integrated_companion_requirements"]
    item_ids = {item["id"] for item in data["items"]}

    assert section["title"] == "项目一体化主线"
    assert section["priority_order"] == [
        "INT-00 产品定位与成功标准",
        "INT-01 身份、设备与个人小芯归属",
        "INT-02 对话主链路与真机输出",
        "INT-03 长期记忆、人格与情绪陪伴",
        "INT-04 小程序低负担消费与控制",
        "INT-05 硬件表达、状态与运行健康",
        "INT-06 发布、部署与生产观测",
        "INT-07 压缩纵向验收与证据闭环",
    ]

    columns = section["columns"]
    assert [column["id"] for column in columns] == [
        "INT-00",
        "INT-01",
        "INT-02",
        "INT-03",
        "INT-04",
        "INT-05",
        "INT-06",
        "INT-07",
    ]

    for column in columns:
        assert column["title"]
        assert column["focus"]
        assert column["implemented"]
        assert column["requirements"]
        assert column["remaining"]
        assert column["acceptance"]
        assert all(item_id in item_ids for item_id in column["related_items"])

    current_state = "\n".join(section["current_state"])
    assert "大一新生" in section["summary"]
    assert "schema v21" in current_state
    assert "qwen3.7-flash" in current_state
    assert "POST /api/xiaoxin/devices/{device_id}/text-chat" in current_state
    assert "真实 7 天或 12 人用户研究不作为当前工期门槛" in current_state
    assert columns[3]["status"] == "active"
    assert "粗分类到细分类" in "\n".join(columns[3]["implemented"])
    assert "双设备" in "\n".join(columns[7]["requirements"])


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


def test_companion_growth_requirements_section_has_expected_sequence_and_links():
    data = load_yaml_data()
    section = data["companion_growth_requirements"]
    item_ids = {item["id"] for item in data["items"]}

    assert section["title"] == "陪伴成长需求分栏"
    assert section["priority_order"] == [
        "CG-00 主体、年龄与关系事实源",
        "CG-01 CompanionMind 实时提交合同",
        "CG-02 Evidence 生命周期与关系时期",
        "CG-03 关系阶段与确定性陪伴策略",
        "CG-04 会话胶囊、自我调整与陪伴章节",
        "CG-05 有依据主动陪伴与反馈闭环",
        "CG-06 学生年级、小芯年龄与跨阶段回望",
        "CG-07 CompanionMind 多端投影与体验验收",
        "CG-YEAR-01 大一到大二成长可感知闭环",
        "CG-IND-01 个体人格与长期成长 V1",
    ]

    columns = section["columns"]
    assert [column["id"] for column in columns] == [
        "CG-00",
        "CG-01",
        "CG-02",
        "CG-03",
        "CG-04",
        "CG-05",
        "CG-06",
        "CG-07",
        "CG-YEAR-01",
        "CG-IND-01",
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


def test_individuality_growth_v1_tracks_implemented_gates_and_open_real_evidence():
    data = load_yaml_data()
    columns = {
        column["id"]: column
        for column in data["companion_growth_requirements"]["columns"]
    }
    requirement = columns["CG-IND-01"]

    assert requirement["status"] == "partial"
    implemented = "\n".join(requirement["implemented"])
    requirements = "\n".join(requirement["requirements"])
    remaining = "\n".join(requirement["remaining"])

    assert "现有 CompanionMind" in implemented
    assert "schema v19" in implemented
    assert "不可跳级" in implemented
    assert "两台已绑定真实设备" in implemented
    assert "探索取向" in requirements
    assert "全部 243 种组合合法" in requirements
    assert "low/medium/high" in requirements
    assert "终身不变" in requirements
    assert "一档相处调整" in requirements
    assert "expression_style" in requirements
    assert "14/90/365" in requirements
    assert "Valence-Arousal" in requirements
    assert "不得重新创建 CompanionMind" in requirements
    assert "真实 ESP32 HIL" in remaining
    assert "双设备文字链路" in remaining
    assert "24 小时" in remaining
    assert "D7" in remaining and "D30" in remaining and "D90" in remaining
    assert "旧 policy" in remaining


def test_companion_productization_requirements_are_the_current_p0_sequence():
    data = load_yaml_data()
    section = data["companion_productization_requirements"]
    item_ids = {item["id"] for item in data["items"]}

    assert section["title"] == "陪伴记忆产品化改进（P0）"
    assert section["priority_order"] == [
        "CP-00 统一 CompanionMind 装配与产品化基线",
        "CP-01 Observation 统一采集与可信 Evidence",
        "CP-02 异步候选记忆提取与确认",
        "CP-03 结构化、FTS 与相关性召回",
        "CP-04 真实关系信号与关系质量策略",
        "CP-05 主动陪伴机会、调度与反馈闭环",
        "CP-08 双向关系需要与连接主动",
        "CP-SEM 语义记忆采集、分级写入与自然召回",
        "CP-06 小程序、控制台与硬件消费",
        "CP-07 真机、真实用户校准与发布门禁",
    ]

    columns = section["columns"]
    assert [column["id"] for column in columns] == [
        "CP-00",
        "CP-01",
        "CP-02",
        "CP-03",
        "CP-04",
        "CP-05",
        "CP-08",
        "CP-SEM",
        "CP-06",
        "CP-07",
    ]
    assert all(column["priority"] == "P0" for column in columns)
    assert columns[0]["status"] == "done"

    for column in columns:
        assert column["title"]
        assert column["focus"]
        assert column["implemented"]
        assert column["requirements"]
        assert column["remaining"]
        assert column["acceptance"]
        assert all(item_id in item_ids for item_id in column["related_items"])

    current_state = "\n".join(section["current_state"])
    assert "1183 passed" in current_state
    assert "1227 passed" in current_state
    assert "进程级 CompanionMind" in current_state
    assert "schema v11" in current_state
    assert "schema v21" in current_state
    assert "主动陪伴生产调度闭环" in current_state
    assert "SQLite FTS5 trigram" in current_state
    assert "qwen3.7-flash" in current_state
    assert "双设备" in current_state
    assert "CP-08" in current_state
    assert "真实 7 天或 12 人用户研究不作为当前工期门槛" in current_state
    assert columns[1]["status"] == "done"
    assert columns[2]["status"] == "done"
    assert columns[3]["status"] == "done"
    assert columns[4]["status"] == "done"
    assert columns[5]["status"] == "done"
    assert columns[6]["status"] == "partial"
    assert columns[7]["status"] == "active"
    assert "CompanionMind 外部 interface 已增加 observe" in "\n".join(
        columns[1]["implemented"]
    )
    cp03_implemented = "\n".join(columns[3]["implemented"])
    assert "companion_evidence_fts" in cp03_implemented
    assert "companion_retrieval_audits" in cp03_implemented
    assert (
        "A54CE1D1ABF8BB54BA2DCCDE025E5DD7FCE8DFC1FBD0D7F494A9A48255906264"
        in cp03_implemented
    )
    cp04_implemented = "\n".join(columns[4]["implemented"])
    assert "companion-policy-v2" in cp04_implemented
    assert "relationship_stage_events" in cp04_implemented
    assert (
        "DEAB0CE53485CC4713D45156854995272C98956696A39F3198015A8115F7CD0D"
        in cp04_implemented
    )
    cp05_implemented = "\n".join(columns[5]["implemented"])
    assert "initiative_opportunities" in cp05_implemented
    assert "LLMInitiativeComposer" in cp05_implemented
    assert "XiaoxinInitiativeDeliveryPort" in cp05_implemented
    assert (
        "A2D918EC30500F0666EBD2FD6244FF4C065542101569F3108C6C17A71ECDAA3D"
        in cp05_implemented
    )
    cp08 = columns[6]
    cp08_text = "\n".join(
        (
            cp08["focus"],
            *cp08["requirements"],
            *cp08["implementation_plan"],
            *cp08["remaining"],
            *cp08["acceptance"],
        )
    )
    assert "connection_bid" in cp08_text
    assert "connection_responded" in cp08_text
    assert "deferred" in cp08_text
    assert "Slice A｜已完成" in cp08_text
    assert "Slice B｜已完成" in cp08_text
    assert "Slice C｜已完成" in cp08_text
    assert "Slice D｜已完成" in cp08_text
    assert "Slice E｜已完成" in cp08_text
    cp_sem = columns[7]
    cp_sem_text = "\n".join(
        (
            cp_sem["focus"],
            *cp_sem["requirements"],
            *cp_sem["implementation_plan"],
            *cp_sem["remaining"],
            *cp_sem["acceptance"],
        )
    )
    assert "MemoryInterpreter" in cp_sem_text
    assert "MemoryRecallPlanner" in cp_sem_text
    assert "recall_companion_memory" in cp_sem_text
    assert "off|shadow|candidate|active_explicit" in cp_sem_text
    assert "不回退为正则画像主路径" in cp_sem_text
    cp06 = columns[8]
    cp06_text = "\n".join(
        (
            cp06["focus"],
            *cp06["requirements"],
            *cp06["remaining"],
            *cp06["acceptance"],
        )
    )
    assert "二级入口" in cp06_text
    assert "对话内自然表达" in cp06_text
    assert "主动陪伴开关" in cp06_text
    assert "关系阶段数字" in cp06_text
    assert "设计小程序记忆/成长/主动设置页面" not in cp06_text


def test_companion_productization_section_validation_reports_bad_priority():
    server = load_server_module()
    data = load_yaml_data()
    broken = deepcopy(data)
    broken["companion_productization_requirements"]["columns"][0][
        "priority"
    ] = "urgent"

    errors = server.validate_requirements(broken)

    assert (
        "companion_productization_requirements.CP-00: "
        "priority='urgent' 未在 taxonomy.priorities 中定义。"
        in errors
    )


def test_integrated_companion_section_validation_reports_bad_related_item():
    server = load_server_module()
    data = load_yaml_data()
    broken = deepcopy(data)
    broken["integrated_companion_requirements"]["columns"][0][
        "related_items"
    ] = ["XIAOXIN-NOT-REAL"]

    errors = server.validate_requirements(broken)

    assert (
        "integrated_companion_requirements.INT-00: "
        "related_items 引用了未知条目 'XIAOXIN-NOT-REAL'。"
        in errors
    )


def test_requirements_ledger_records_the_operator_memory_workbench_rollout():
    data = load_yaml_data()
    milestones = {milestone["id"]: milestone for milestone in data["milestones"]}
    items = {item["id"]: item for item in data["items"]}
    columns = {
        column["id"]: column
        for column in data["companion_growth_requirements"]["columns"]
    }

    assert "开发者记忆工作台" in milestones["M4"]["summary"]
    assert "服务端 CompanionMind V2" in milestones["M8"]["summary"]
    assert "已完成" in milestones["M8"]["summary"]
    assert "CG-YEAR-01" in milestones["M8"]["summary"]
    assert "小程序成长摘要" in milestones["M8"]["summary"]
    assert "固件 Overview v1" in milestones["M8"]["summary"]
    assert "待实施" not in milestones["M8"]["summary"]

    current_state = "\n".join(data["companion_growth_requirements"]["current_state"])
    assert "开发者记忆工作台" in current_state
    assert "最多 250 条 Evidence" in current_state
    assert "inactive" in current_state
    assert "九类" in current_state
    assert "1027 passed" in current_state
    assert "1024 passed" not in current_state
    assert "requirements workbench 为 14 passed" in current_state

    cg07_implemented = "\n".join(columns["CG-07"]["implemented"])
    assert "开发者记忆工作台" in cg07_implemented
    assert "miniprogram" in cg07_implemented
    assert "不获得" in cg07_implemented

    console_implemented = "\n".join(items["XIAOXIN-004"]["implemented"])
    assert "Evidence 时间线" in console_implemented
    assert "归属链" in console_implemented
    assert "九类" in console_implemented


def test_companion_growth_requirements_use_v2_semantics():
    data = load_yaml_data()
    columns = {
        column["id"]: column
        for column in data["companion_growth_requirements"]["columns"]
    }

    cg00 = "\n".join(columns["CG-00"]["requirements"])
    assert "学生资料中的当前年级" in cg00
    assert "大一到大四分别映射 1 岁到 4 岁" in cg00
    assert "未知" in cg00 and "null" in cg00
    assert "companion_started_at" not in cg00

    cg02 = "\n".join(columns["CG-02"]["requirements"])
    assert "user Evidence" in cg02
    assert "relationship Evidence" in cg02
    assert "关系重置" in cg02
    assert "保留" in cg02 and "停用" in cg02

    cg03 = "\n".join(columns["CG-03"]["requirements"])
    assert "CompanionPolicy" in cg03
    assert "关系阶段" in cg03
    assert "裸等级" in cg03

    cg04 = "\n".join(columns["CG-04"]["requirements"])
    assert "SessionCapsule" in cg04
    assert "CompanionAdjustment" in cg04
    assert "CompanionChapter" in cg04
    assert "可选索引" in cg04
    assert "C 语言学习" not in cg04

    cg05 = "\n".join(columns["CG-05"]["requirements"])
    assert "Evidence" in cg05
    assert "静默" in cg05
    assert "冷却" in cg05
    assert "拒绝" in cg05

    cg06 = "\n".join(columns["CG-06"]["acceptance"])
    assert "大二首次使用" in cg06
    assert "2 岁" in cg06
    assert "first_meeting" in cg06
    assert "共同经历" in cg06

    cg07 = "\n".join(columns["CG-07"]["requirements"])
    assert "CompanionMind" in cg07
    assert "voice" in cg07
    assert "miniprogram" in cg07
    assert "hardware" in cg07


def test_companion_memory_v2_product_items_and_decisions_are_authoritative():
    data = load_yaml_data()
    items = {item["id"]: item for item in data["items"]}
    decisions = {decision["id"]: decision for decision in data["decisions"]}

    age_item = items["XIAOXIN-PROD-019"]
    age_contract = "\n".join(
        [age_item["title"], age_item["summary"], *age_item["acceptance"]]
    )
    assert "学生年级" in age_contract
    assert "小芯年龄" in age_contract
    assert "未知" in age_contract and "null" in age_contract
    assert "陪伴年轮" not in age_contract
    age_implemented = "\n".join(age_item["implemented"])
    age_remaining = "\n".join(age_item["remaining"])
    assert "academic_stage" in age_implemented
    assert "xiaoxin_age=1/2/3/4/null" in age_implemented
    assert "接入学生资料当前年级" not in age_remaining
    assert "小程序首页和固件 Overview" in age_implemented
    assert "真机" in age_remaining

    relationship_item = items["XIAOXIN-PROD-020"]
    assert "关系阶段" in relationship_item["title"]
    assert "关系成熟度" not in relationship_item["title"]

    chapter_item = items["XIAOXIN-PROD-021"]
    assert "陪伴章节" in chapter_item["title"]
    assert "学年回顾" not in chapter_item["title"]
    chapter_implemented = "\n".join(chapter_item["implemented"])
    chapter_remaining = "\n".join(chapter_item["remaining"])
    assert "operator 开发者记忆工作台" in chapter_implemented
    assert "血缘" in chapter_implemented
    assert "学生侧" in chapter_remaining

    control_contract = "\n".join(items["XIAOXIN-PROD-022"]["acceptance"])
    assert "关系重置" in control_contract
    assert "user Evidence" in control_contract
    assert "relationship Evidence" in control_contract
    control_implemented = "\n".join(items["XIAOXIN-PROD-022"]["implemented"])
    control_remaining = "\n".join(items["XIAOXIN-PROD-022"]["remaining"])
    assert "七类" in control_implemented
    assert "显式确认" in control_implemented
    assert "miniprogram" in control_implemented
    assert "小程序" in control_remaining

    assert "关系阶段" in items["XIAOXIN-018"]["title"]
    assert "SessionCapsule" in items["XIAOXIN-019"]["summary"]
    assert "当前年级" in items["XIAOXIN-020"]["summary"]

    decision_005 = decisions["DEC-005"]["decision"]
    assert "小芯年龄" in decision_005
    assert "相处时长" in decision_005
    assert "关系阶段" in decision_005
    assert "陪伴年轮" not in decision_005

    decision_006 = decisions["DEC-006"]["decision"]
    assert "CompanionPolicy" in decision_006
    assert "基础能力" in decision_006

    decision_007 = decisions["DEC-007"]["decision"]
    assert "memory_subject_id" in decision_007
    assert "说话人隔离" in decision_007
    assert "不承担小芯年龄" in decision_007


def test_companion_memory_v2_requirements_reference_authoritative_rollout_docs():
    data = load_yaml_data()
    columns = data["companion_growth_requirements"]["columns"]
    remaining = "\n".join(
        entry for column in columns for entry in column["remaining"]
    )
    acceptance = "\n".join(
        entry for column in columns for entry in column["acceptance"]
    )

    design_path = (
        "docs/superpowers/specs/"
        "2026-07-18-xiaoxin-companion-memory-v2-design.md"
    )
    plan_path = (
        "docs/superpowers/plans/"
        "2026-07-18-xiaoxin-companion-memory-v2-implementation.md"
    )
    assert design_path in remaining
    assert plan_path in remaining
    assert design_path in acceptance
    assert plan_path in acceptance


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


def test_companion_growth_section_validation_reports_bad_priority_and_unknown_related_item():
    server = load_server_module()
    data = load_yaml_data()
    broken = deepcopy(data)
    broken["companion_growth_requirements"]["columns"][0]["priority"] = "urgent"
    broken["companion_growth_requirements"]["columns"][0]["related_items"] = [
        "XIAOXIN-NOT-REAL"
    ]

    errors = server.validate_requirements(broken)

    assert (
        "companion_growth_requirements.CG-00: priority='urgent' 未在 taxonomy.priorities 中定义。"
        in errors
    )
    assert (
        "companion_growth_requirements.CG-00: related_items 引用了未知条目 'XIAOXIN-NOT-REAL'。"
        in errors
    )


def test_requirements_html_uses_generic_requirement_section_renderer():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function renderRequirementSection(" in html
    assert 'renderRequirementSection("integrated_companion_requirements"' in html
    assert 'renderRequirementSection("mini_program_requirements"' in html
    assert 'renderRequirementSection("hardware_requirements"' in html
    assert 'renderRequirementSection("companion_growth_requirements"' in html
    assert 'renderRequirementSection("companion_productization_requirements"' in html
    assert "data-section-item" in html


def test_requirements_html_has_workbench_quick_navigation():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function renderWorkbenchNavigation(" in html
    assert "data-jump-target" in html
    assert "requirements-section-integrated_companion_requirements" in html
    assert "requirements-section-mini_program_requirements" in html
    assert "requirements-section-hardware_requirements" in html
    assert "requirements-section-companion_growth_requirements" in html
    assert "requirements-section-companion_productization_requirements" in html
    assert "requirements-section-matrix" in html
    assert "scrollIntoView" in html
    assert "工作台导航" in html
    assert "快速定位" in html
    assert "模块筛选" in html
    assert "一体化主线" in html
    assert "小程序需求" in html
    assert "硬件端需求" in html
    assert "陪伴成长需求" in html
    assert "陪伴改进 P0" in html
    assert "服务端状态矩阵" in html


def test_requirements_html_has_readable_chinese_copy_without_mojibake():
    html = HTML_PATH.read_text(encoding="utf-8")

    expected_copy = [
        "Xiaoxin 项目状态工作台",
        "正在加载 Xiaoxin 项目状态...",
        "总条目",
        "当前可见",
        "已完成",
        "部分完成",
        "P0 未闭环",
        "产品需求",
        "一体化主线",
        "小程序条目",
        "硬件条目",
        "陪伴成长条目",
        "陪伴改进条目",
        "风险",
        "搜索",
        "标题、摘要、模块、证据...",
        "领域",
        "模块",
        "状态",
        "优先级",
        "类型",
        "阶段",
        "全部",
        "判断",
        "下一步推荐",
        "当前状态",
        "需求详情",
        "已实现",
        "需求",
        "实施计划",
        "还需要做",
        "验收标准",
        "关联条目",
        "暂无。",
        "全部模块",
        "没有匹配的条目。",
        "状态矩阵",
        "选择一个条目查看详情。",
        "条目详情",
        "可优化",
        "证据",
        "关联",
        "里程碑路线",
        "缓解：",
        "关键决策",
        "版本",
        "更新",
        "负责人",
        "来源",
        "学生侧小程序需求",
        "全部小程序需求",
        "项目一体化主线",
        "全部一体化主线",
        "硬件端需求",
        "全部硬件需求",
        "陪伴成长需求",
        "全部陪伴成长需求",
        "陪伴记忆产品化改进（P0）",
        "全部陪伴改进",
        "启动方式：进入",
        "无法加载 requirements.json",
        "YAML 校验失败",
        "未知错误",
        "无法连接本地工作台服务",
        "直接打开 HTML 文件时无法读取 YAML。",
        "错误：",
    ]
    mojibake_markers = [
        "椤圭洰",
        "鐘舵",
        "鏉＄洰",
        "妯″潡",
        "鍏ㄩ儴",
        "绫诲瀷",
        "闃舵",
        "閫夋嫨",
        "瀛︾敓",
        "纭欢",
        "閸忋劑",
        "???",
    ]

    for copy in expected_copy:
        assert copy in html
    for marker in mojibake_markers:
        assert marker not in html
