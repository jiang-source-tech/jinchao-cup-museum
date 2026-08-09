import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
UPDATES_DIR = REPO_ROOT / "docs" / "updates"
INDEX_PATH = UPDATES_DIR / "README.md"
TEMPLATE_PATH = UPDATES_DIR / "UPDATE_TEMPLATE.md"
DETAIL_PATH = (
    UPDATES_DIR
    / "2026"
    / "2026-07-11-notification-tts-reliable-playback.md"
)

TEMPLATE_SECTIONS = (
    "## 1. 更新信息",
    "## 2. 更新摘要",
    "## 3. 更新背景",
    "## 4. 修改目标与非目标",
    "## 5. 修改前后对比",
    "## 6. 系统流程变化",
    "## 7. 详细修改内容",
    "## 8. 协议、配置与数据变化",
    "## 9. 影响范围与兼容性",
    "## 10. 部署与迁移说明",
    "## 11. 验证结果",
    "## 12. 风险与已知限制",
    "## 13. 回滚方案",
    "## 14. 运行观察项",
    "## 15. 后续事项",
    "## 16. 关联提交与文档",
    "## 17. 文档修订记录",
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_update_framework_files_exist():
    assert INDEX_PATH.is_file()
    assert TEMPLATE_PATH.is_file()


def test_index_defines_project_wide_update_policy():
    content = read_text(INDEX_PATH)

    for required in (
        "整个项目",
        "Asia/Shanghai",
        "UTC+8",
        "UPD-YYYYMMDD-NNN",
        "最新记录放在最上方",
        "已发布",
        "已回滚",
        "实机验收",
        "生产验证",
    ):
        assert required in content


def test_template_covers_development_test_and_operations():
    content = read_text(TEMPLATE_PATH)

    for section in TEMPLATE_SECTIONS:
        assert section in content

    for required in (
        "开发人员",
        "测试人员",
        "运维人员",
        "已验证事实",
        "推断",
        "未知项",
        "部署顺序",
        "回滚",
    ):
        assert required in content


def test_first_update_is_indexed_with_a_valid_relative_link():
    index = read_text(INDEX_PATH)
    relative_detail = "2026/2026-07-11-notification-tts-reliable-playback.md"

    assert "UPD-20260711-001" in index
    assert "2026-07-11 07:19（UTC+8）" in index
    assert f"]({relative_detail})" in index
    assert DETAIL_PATH.is_file()


def test_first_update_records_transport_behavior_and_evidence():
    content = read_text(DETAIL_PATH)

    for required in (
        "UPD-20260711-001",
        "2026-07-11 07:19:45（UTC+8）",
        "具体推送时间未记录",
        "Doorbell MQTT",
        "WebSocket/TCP",
        "tts:start",
        "ready",
        "done",
        "error",
        "delivery_id",
        "sentence_id",
        "output_write_timeout",
        "5a99cd0aeb97fed2233717276d4f2d906f37742c",
        "1b483480717bf333f4bef176e911d761834503d7",
        "487 passed, 1 skipped",
        "264 passed",
        "9 个",
        "5,673,808",
        "55%",
        "实机验收",
        "未执行",
    ):
        assert required in content

    assert "CPU 不足是已确认根因" not in content
    assert "物理端到端播放通过" not in content


def test_first_update_keeps_validation_levels_separate():
    content = read_text(DETAIL_PATH)

    assert "| 实机验收 | 未执行 |" in content
    assert "| 生产验证 | 未记录 |" in content

    false_success = re.compile(
        r"(?:实机|真机|物理端到端).{0,24}(?:通过|成功|已验证)",
        re.IGNORECASE,
    )
    assert false_success.search(content) is None


def test_first_update_keeps_cpu_root_cause_unproven():
    content = read_text(DETAIL_PATH)

    assert "CPU、I2S 和调度压力仍需通过真实设备测量" in content
    assert "没有实机性能数据支持" in content

    false_root_cause = re.compile(
        r"(?:(?:CPU|处理器).{0,24}(?:已确认|已经证实|确定).{0,24}(?:根因|原因)"
        r"|(?:已确认|已经证实|确定).{0,24}(?:CPU|处理器).{0,24}(?:根因|原因))",
        re.IGNORECASE,
    )
    assert false_root_cause.search(content) is None


def test_formal_update_has_no_unexplained_placeholders():
    content = read_text(DETAIL_PATH)
    unfinished_markers = ("T" + "BD", "T" + "ODO", "FIX" + "ME")
    forbidden = re.compile(
        rf"\b(?:{'|'.join(unfinished_markers)})\b",
        re.IGNORECASE,
    )

    assert forbidden.search(content) is None


def test_update_document_local_links_resolve():
    markdown_link = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    documents = sorted(UPDATES_DIR.rglob("*.md"))
    assert INDEX_PATH in documents
    assert TEMPLATE_PATH in documents
    assert DETAIL_PATH in documents

    for document in documents:
        content = read_text(document)
        for raw_target in markdown_link.findall(content):
            if raw_target.startswith(("http://", "https://", "#")):
                continue
            target_without_anchor = raw_target.split("#", 1)[0]
            target = (document.parent / target_without_anchor).resolve()
            assert target.exists(), f"broken link in {document}: {raw_target}"
