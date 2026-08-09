# Project Update Records Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable project-wide update record system with a chronological index, a reusable cross-role template, and a fully evidenced first record for the reliable notification TTS release.

**Architecture:** `docs/updates/README.md` is the stable entry point and reverse-chronological index. `docs/updates/UPDATE_TEMPLATE.md` defines the mandatory information contract for future updates, while dated files under `docs/updates/YYYY/` contain complete release-specific evidence. A focused pytest contract protects required sections, links, timestamps, commit hashes, verification boundaries, and the distinction between automated and physical validation.

**Tech Stack:** Markdown, Python 3.12, pytest 7.4, `pathlib`, regular expressions, Git.

## Global Constraints

- All recorded times use Asia/Shanghai and display `UTC+8` explicitly.
- Never invent an exact time. The two merge commits are timestamped `2026-07-11 07:19:45+08:00`; the exact GitHub push minute was not retained and must be described as unrecorded.
- Only merged and pushed changes may use status `已发布`.
- Automated tests, target build, real-device acceptance, and production validation are separate evidence levels.
- The first update ID is exactly `UPD-20260711-001`.
- The first detail path is exactly `docs/updates/2026/2026-07-11-notification-tts-reliable-playback.md`.
- Do not modify `docs/README.md` or `docs/operations/xiaoxin-real-device-acceptance-ledger.md`.
- Do not claim that screen refresh or CPU starvation is the proven root cause.
- Do not claim physical end-to-end playback passed; no serial/USB device was available.
- Doorbell MQTT is notification/wake transport. Reliable TTS control, binary audio, and ACKs use the ordered WebSocket/TCP path.

---

## File Map

- Create `docs/updates/README.md`: policy, vocabulary, maintenance workflow, and reverse-chronological update index.
- Create `docs/updates/UPDATE_TEMPLATE.md`: mandatory cross-role update detail template.
- Create `docs/updates/2026/2026-07-11-notification-tts-reliable-playback.md`: first complete project update record.
- Create `main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py`: executable documentation contract for structure, evidence, and links.

### Task 1: Establish the update index and reusable template

**Files:**
- Create: `docs/updates/README.md`
- Create: `docs/updates/UPDATE_TEMPLATE.md`
- Create: `main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py`

**Interfaces:**
- Consumes: approved design `docs/superpowers/specs/2026-07-11-project-update-record-design.md`.
- Produces: `UPDATES_DIR`, `INDEX_PATH`, `TEMPLATE_PATH`, `DETAIL_PATH` constants and the stable section vocabulary used by Task 2.

- [ ] **Step 1: Write the failing framework contract tests**

Create `main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py` with:

```python
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
```

- [ ] **Step 2: Run the framework tests and confirm RED**

Run:

```powershell
python -m pytest tests/xiaoxin/test_update_docs_contract.py -q
```

Working directory: `main/xiaozhi-server`

Expected: FAIL because `docs/updates/README.md` and `docs/updates/UPDATE_TEMPLATE.md` do not exist.

- [ ] **Step 3: Create the project update index**

Create `docs/updates/README.md` with these exact top-level elements:

```markdown
# 项目更新记录

本目录记录整个项目已经实施或发布的正式更新，供开发人员、测试人员和运维人员共同使用。所有时间统一采用 Asia/Shanghai，并标注 UTC+8。

## 使用规则

- 更新编号使用 `UPD-YYYYMMDD-NNN`。
- 最新记录放在最上方。
- 只有已经合并并推送到目标分支的更新才能标记为“已发布”。
- 自动化测试、目标构建、实机验收和生产验证必须分别记录。
- 无法核实的时间或结论必须写明“具体时间未记录”或“未知”，不得推测。
- 新记录从 [UPDATE_TEMPLATE.md](UPDATE_TEMPLATE.md) 复制，并在完成详情后加入本索引。

## 状态词汇

| 状态 | 含义 |
|---|---|
| 设计中 | 已确定方向，尚未开始实现 |
| 开发中 | 正在实现，不能作为已发布能力使用 |
| 待验证 | 已实现，但缺少规定的验证证据 |
| 已发布 | 已合并并推送到目标分支 |
| 已回滚 | 发布后已撤销 |
| 已废弃 | 不再使用且有替代方案或终止说明 |

## 更新类型

功能、缺陷修复、可靠性、性能、安全、配置、文档、基础设施。

## 更新索引

| 更新时间（UTC+8） | 更新编号 | 更新主题 | 更新类型 | 涉及范围 | 状态 | 详情 |
|---|---|---|---|---|---|---|

## 维护流程

1. 从模板复制新的详情文件。
2. 核对时间、提交哈希、配置值、测试数字和文档链接。
3. 分开记录已验证事实、推断和未知项。
4. 在本页索引表顶部加入新记录。
5. 后续补做实机验收或生产验证时，更新详情页的验证章节和修订记录。
```

- [ ] **Step 4: Create the reusable update template**

Create `docs/updates/UPDATE_TEMPLATE.md`. It must contain all 17 headings in `TEMPLATE_SECTIONS` and the following instructions under the relevant headings:

```markdown
# UPD-YYYYMMDD-NNN：更新主题

> 本模板供开发人员、测试人员和运维人员共同使用。正式详情必须删除模板提示语；不能确认的信息应明确记录未知原因，不得保留未解释的占位内容。

## 1. 更新信息

记录更新编号、主题、状态、更新类型、开始时间、合并时间、发布时间、时区、涉及仓库、目标分支和关联提交。精确时间没有证据时写“具体时间未记录”。

## 2. 更新摘要

用非实现人员也能理解的语言说明问题、变化和最终效果。

## 3. 更新背景

分别记录原始现象、已确认原因、高概率推断和未知项。明确区分已验证事实、推断和未知项。

## 4. 修改目标与非目标

列出本次必须达到的结果，以及明确不在本次更新中解决的内容。

## 5. 修改前后对比

使用场景、修改前行为、修改后行为、用户影响四列进行对比。

## 6. 系统流程变化

记录修改前和修改后的事件或数据流，必要时使用文本流程图。

## 7. 详细修改内容

按服务端、固件、前端、协议、基础设施或其他实际组件分节，写明模块、行为和关键文件。

## 8. 协议、配置与数据变化

分别说明协议字段、配置默认值、数据库或持久化格式是否变化；没有变化时明确写“无”。

## 9. 影响范围与兼容性

记录用户、性能、网络、存储、旧版本和跨版本部署影响。

## 10. 部署与迁移说明

给出可执行的部署顺序、前置条件、兼容窗口和迁移操作。

## 11. 验证结果

分别记录自动化测试、构建、实机验收和生产验证。测试人员必须能根据命令或证据复核结果。

## 12. 风险与已知限制

记录仍未解决的问题、适用边界和错误使用可能造成的后果。

## 13. 回滚方案

记录回滚目标、回滚顺序、配置恢复方式和回滚后失去的能力，供运维人员直接执行。

## 14. 运行观察项

记录日志字段、指标、告警信号和异常判定方式。

## 15. 后续事项

记录需要补做的测试、监控、文档或低优先级改进。

## 16. 关联提交与文档

列出仓库、完整提交哈希和相关设计、协议、部署或验收文档。

## 17. 文档修订记录

记录修订时间、修订内容和修订依据，不修改原始发布时间。
```

- [ ] **Step 5: Run the framework tests and confirm GREEN**

Run:

```powershell
python -m pytest tests/xiaoxin/test_update_docs_contract.py -q
```

Working directory: `main/xiaozhi-server`

Expected: `3 passed`.

- [ ] **Step 6: Commit the framework**

```powershell
git add docs/updates/README.md docs/updates/UPDATE_TEMPLATE.md main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py
git commit -m "docs: add project update record framework"
```

### Task 2: Record the reliable notification TTS release

**Files:**
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py`
- Modify: `docs/updates/README.md`
- Create: `docs/updates/2026/2026-07-11-notification-tts-reliable-playback.md`

**Interfaces:**
- Consumes: constants and template vocabulary created in Task 1.
- Produces: the first indexed update record, `UPD-20260711-001`.

- [ ] **Step 1: Add failing tests for the first update record**

Append to `test_update_docs_contract.py`:

```python
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


def test_formal_update_has_no_unexplained_placeholders():
    content = read_text(DETAIL_PATH)
    unfinished_markers = ("T" + "BD", "T" + "ODO", "FIX" + "ME")
    forbidden = re.compile(
        rf"\b(?:{'|'.join(unfinished_markers)})\b",
        re.IGNORECASE,
    )

    assert forbidden.search(content) is None
```

- [ ] **Step 2: Run the first-record tests and confirm RED**

Run:

```powershell
python -m pytest tests/xiaoxin/test_update_docs_contract.py -q
```

Working directory: `main/xiaozhi-server`

Expected: the three new tests FAIL because the detail file and index row do not exist.

- [ ] **Step 3: Add the first row to the update index**

Insert directly below the index header in `docs/updates/README.md`:

```markdown
| 2026-07-11 07:19（UTC+8） | UPD-20260711-001 | 通知 TTS 可靠播放与屏保唤醒 | 缺陷修复、可靠性 | 服务端、固件、协议、配置、文档 | 已发布 | [查看详情](2026/2026-07-11-notification-tts-reliable-playback.md) |
```

- [ ] **Step 4: Create the complete TTS update detail**

Create `docs/updates/2026/2026-07-11-notification-tts-reliable-playback.md` with all 17 template sections and the following exact factual content:

- Update ID: `UPD-20260711-001`.
- Status: `已发布`.
- Merge time for both repositories: `2026-07-11 07:19:45（UTC+8）` from Git commit metadata.
- Publication statement: both `main` branches were pushed to GitHub on 2026-07-11; `具体推送时间未记录`.
- Original symptom: notification TTS could stutter or lose its beginning while the device was in power-saving/screensaver state.
- Root-cause boundary: simultaneous screen wake and playback preparation was a confirmed sequencing risk; CPU starvation from screen refresh remains a hypothesis requiring device measurement.
- Old behavior: start and audio could overlap screen/audio preparation; server sending completion was not equivalent to physical playback completion.
- New behavior: `MQTT 通知 → WebSocket tts:start → 屏幕/音频准备 → ready → 二进制音频 → 输出排空 → done/error → 完成或整句重试`.
- Server changes: READY_WAIT/STREAMING/DONE_WAIT/TERMINAL lifecycle, per-device attempt lease, full-text retry, typed ACK failures, active-phase TTL protection, exact-done precedence on connection close, delivery and sentence correlation.
- Firmware changes: notification-origin token, return-state inheritance, generation/epoch isolation, pre-roll, typed decoder/resampler/output failures, bounded Waveshare I2S write, `output_write_timeout`, pager fallback propagation.
- Transport statement: Doorbell MQTT only wakes/notifies; TTS JSON, audio and ACK use the same ordered WebSocket/TCP connection; MQTT/UDP audio is legacy and does not advertise strict reliability.
- Configuration values: ready timeout 700 ms, done timeout 10,000 ms, start retry delays `[300, 600, 1200]` ms, delivery retry delays `[2000, 5000, 15000, 30000]` ms.
- Compatibility: legacy devices continue through `legacy_unverified`; strong guarantees require boolean `tts_ready_ack`, `tts_done_ack`, and `tts_preroll_buffer` all equal to true.
- Verification: server `487 passed, 1 skipped`; firmware `264 passed`; 9 relevant host executables compiled and passed; ESP-IDF v5.5.4 Waveshare 1.46 fullclean build passed; `ai_pet.bin` 5,673,808 bytes; app partition 55% free.
- Physical boundary: no serial/USB device was present, so real-device acceptance was not executed and physical end-to-end playback is not claimed.
- Deployment order: deploy compatible server first, observe legacy compatibility, build/flash firmware, verify advertised capabilities, run screensaver/power-save/disconnect/supersession/output-timeout acceptance cases.
- Rollback: server target `189fd7528cfced80d0c4dcca58afbbd9cb4a7165`; firmware target `a5e0349d06ea394f5a4b0c76e5f51bfede12e12a`; roll back firmware first if new capability advertisement must stop, then server, and re-run legacy notification checks.
- Observability: `delivery_id`, `sentence_id`, `start_to_ready_ms`, `done_wait_ms`, `output_write_timeout`, WebSocket close reason, retry delay and terminal state.
- Links: `../../development/xiaoxin-tts-playback-ack.md`, `../../superpowers/specs/2026-07-10-notification-tts-screen-wake-preroll-design.zh-CN.md`, and `../../superpowers/plans/2026-07-10-notification-tts-reliable-playback.md`.

The before/after section must use a table covering screensaver start, audio send timing, completion criteria, disconnect, stale audio, state restoration, and output timeout. The verification section must use a table with separate rows for server tests, firmware Python tests, host tests, target build, real-device acceptance, and production validation.

- [ ] **Step 5: Run the complete documentation contract**

Run:

```powershell
python -m pytest tests/xiaoxin/test_update_docs_contract.py -q
```

Working directory: `main/xiaozhi-server`

Expected: `6 passed`.

- [ ] **Step 6: Commit the first update record**

```powershell
git add docs/updates/README.md docs/updates/2026/2026-07-11-notification-tts-reliable-playback.md main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py
git commit -m "docs: record reliable notification tts update"
```

### Task 3: Validate links, evidence boundaries, and repository scope

**Files:**
- Modify: `main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py`

**Interfaces:**
- Consumes: all documents created by Tasks 1 and 2.
- Produces: a generic local-link integrity check and final verification evidence.

- [ ] **Step 1: Add a failing local-link integrity test**

Append to `test_update_docs_contract.py`:

```python
def test_update_document_local_links_resolve():
    markdown_link = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for document in (INDEX_PATH, DETAIL_PATH):
        content = read_text(document)
        for raw_target in markdown_link.findall(content):
            if raw_target.startswith(("http://", "https://", "#")):
                continue
            target_without_anchor = raw_target.split("#", 1)[0]
            target = (document.parent / target_without_anchor).resolve()
            assert target.exists(), f"broken link in {document}: {raw_target}"
```

- [ ] **Step 2: Prove the link test detects a broken link**

Temporarily change one detail link in `docs/updates/README.md` to a nonexistent filename, then run:

```powershell
python -m pytest tests/xiaoxin/test_update_docs_contract.py::test_update_document_local_links_resolve -q
```

Working directory: `main/xiaozhi-server`

Expected: FAIL and identify the nonexistent target. Restore the correct link immediately after observing RED.

- [ ] **Step 3: Run the focused contract after restoring the link**

Run:

```powershell
python -m pytest tests/xiaoxin/test_update_docs_contract.py -q
```

Working directory: `main/xiaozhi-server`

Expected: `7 passed`.

- [ ] **Step 4: Run repository-level documentation checks**

Run from the repository root:

```powershell
git diff --check HEAD~2..HEAD
$unfinished = ('T' + 'BD') + '|' + ('T' + 'ODO') + '|' + ('FIX' + 'ME')
rg -n $unfinished docs/updates/README.md docs/updates/2026/2026-07-11-notification-tts-reliable-playback.md
git diff --name-only HEAD~2..HEAD
```

Expected:

- `git diff --check` exits 0.
- `rg` returns no matches in the formal index or first update detail.
- changed files are limited to `docs/updates/**` and `main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py`.
- `docs/README.md` and `docs/operations/xiaoxin-real-device-acceptance-ledger.md` are absent from the diff.

- [ ] **Step 5: Run the full server test suite**

Run:

```powershell
python -m pytest
```

Working directory: `main/xiaozhi-server`

Expected: all tests pass; the existing environment may retain the known skipped async management-client test.

- [ ] **Step 6: Commit the final contract hardening**

```powershell
git add main/xiaozhi-server/tests/xiaoxin/test_update_docs_contract.py
git commit -m "test: enforce project update documentation contract"
```

## Final Review Checklist

- [ ] Every design requirement maps to a task and executable assertion.
- [ ] The first record uses verified merge time and explicitly states that push time was not retained.
- [ ] The first record distinguishes confirmed sequencing defects from the unproven CPU-starvation hypothesis.
- [ ] MQTT and WebSocket responsibilities match the deployed topology.
- [ ] Test and build numbers match the merged `main` verification evidence.
- [ ] Real-device and production validation remain explicitly unverified.
- [ ] Rollback commits are exact and deployment order is actionable.
- [ ] Index and detail links resolve.
- [ ] Protected documentation files remain untouched.
