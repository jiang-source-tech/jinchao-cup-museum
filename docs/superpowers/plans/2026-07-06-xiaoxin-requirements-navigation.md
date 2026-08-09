# Xiaoxin Requirements Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the requirements workbench left sidebar from module-only navigation to a workbench navigation that can jump to mini-program requirements, hardware requirements, and the service status matrix.

**Architecture:** Keep the workbench as a single static HTML page backed by `requirements.yaml`. Add stable section IDs, replace `renderModules(items)` with `renderWorkbenchNavigation(items)`, and add a separate `data-jump-target` click handler that scrolls without changing filters.

**Tech Stack:** Static HTML/CSS/JavaScript, Python 3.12, pytest.

## Global Constraints

- Do not change `requirements.yaml`.
- Do not introduce tabs or hide any area.
- Do not add scroll-position auto-highlighting in the first version.
- Quick-locate buttons must not change search, status, priority, kind, milestone, or module filters.
- Module filtering behavior must remain unchanged.

---

## File Structure

- Modify `docs/requirements/test_requirements_workbench.py`: add static assertions for the new navigation hooks.
- Modify `docs/requirements/requirements.html`: add stable target IDs, render workbench navigation, and handle `data-jump-target` clicks.

---

### Task 1: Add Navigation Contract Test

**Files:**
- Modify: `docs/requirements/test_requirements_workbench.py`

**Interfaces:**
- Consumes: Current static HTML source from `docs/requirements/requirements.html`
- Produces: `test_requirements_html_has_workbench_quick_navigation()` asserting the navigation contract.

- [ ] **Step 1: Add the failing test**

Append this test to `docs/requirements/test_requirements_workbench.py`:

```python
def test_requirements_html_has_workbench_quick_navigation():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function renderWorkbenchNavigation(" in html
    assert "data-jump-target" in html
    assert "requirements-section-mini_program_requirements" in html
    assert "requirements-section-hardware_requirements" in html
    assert "requirements-section-matrix" in html
    assert "scrollIntoView" in html
    assert "工作台导航" in html
    assert "快速定位" in html
    assert "模块筛选" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest docs\requirements\test_requirements_workbench.py -q
```

Expected: FAIL with `test_requirements_html_has_workbench_quick_navigation` failing because `renderWorkbenchNavigation`, `data-jump-target`, target IDs, and `scrollIntoView` do not exist yet.

- [ ] **Step 3: Commit the failing test**

Run:

```powershell
git add docs\requirements\test_requirements_workbench.py
git commit -m "test: cover requirements workbench quick navigation"
```

Expected: commit succeeds with only `docs/requirements/test_requirements_workbench.py` staged.

---

### Task 2: Implement Workbench Quick Navigation

**Files:**
- Modify: `docs/requirements/requirements.html`
- Test: `docs/requirements/test_requirements_workbench.py`

**Interfaces:**
- Consumes: `state.data.mini_program_requirements`, `state.data.hardware_requirements`, existing `state.filters.module`
- Produces:
  - `renderWorkbenchNavigation(items) -> string`
  - `renderQuickJumpButton(targetId: string, label: string, count: number | string) -> string`
  - `attachHandlers()` support for `[data-jump-target]`

- [ ] **Step 1: Add stable IDs to requirement sections**

In `renderRequirementSection(sectionKey, options)`, replace:

```javascript
          <section class="panel miniprogram-panel">
```

with:

```javascript
          <section id="requirements-section-${escapeHtml(sectionKey)}" class="panel miniprogram-panel">
```

- [ ] **Step 2: Replace `renderModules(items)` with `renderWorkbenchNavigation(items)`**

In `docs/requirements/requirements.html`, replace the entire `renderModules` function, from the line `function renderModules(items) {` through the closing brace immediately before `function renderTable(items) {`, with:

```javascript
      function renderWorkbenchNavigation(items) {
        const counts = countBy(state.data.items, "module");
        const modulesByArea = {};
        for (const module of state.data.modules || []) {
          modulesByArea[module.area] ||= [];
          modulesByArea[module.area].push(module);
        }

        const quickJumps = [
          state.data.mini_program_requirements?.columns?.length
            ? renderQuickJumpButton(
                "requirements-section-mini_program_requirements",
                "小程序需求",
                state.data.mini_program_requirements.columns.length
              )
            : "",
          state.data.hardware_requirements?.columns?.length
            ? renderQuickJumpButton(
                "requirements-section-hardware_requirements",
                "硬件端需求",
                state.data.hardware_requirements.columns.length
              )
            : "",
          renderQuickJumpButton(
            "requirements-section-matrix",
            "服务端状态矩阵",
            items.length
          ),
        ].join("");

        const groups = Object.entries(modulesByArea)
          .map(([area, modules]) => {
            const buttons = modules
              .map((module) => {
                const active = state.filters.module === module.id ? "active" : "";
                return `
                  <li>
                    <button class="module-button ${active}" data-module="${escapeHtml(module.id)}">
                      <span>${escapeHtml(module.name)}</span>
                      <span class="module-count">${counts[module.id] || 0}</span>
                    </button>
                  </li>
                `;
              })
              .join("");
            return `
              <li class="module-group">${escapeHtml(taxonomyLabel("areas", area))}</li>
              ${buttons}
            `;
          })
          .join("");

        const allActive = state.filters.module === "all" ? "active" : "";
        return `
          <aside class="panel">
            <div class="panel-header">工作台导航</div>
            <ul class="module-list">
              <li class="module-group">快速定位</li>
              ${quickJumps}
              <li class="module-group">模块筛选</li>
              <li>
                <button class="module-button ${allActive}" data-module="all">
                  <span>全部模块</span>
                  <span class="module-count">${state.data.items.length}</span>
                </button>
              </li>
              ${groups}
            </ul>
          </aside>
        `;
      }

      function renderQuickJumpButton(targetId, label, count) {
        return `
          <li>
            <button class="module-button" data-jump-target="${escapeHtml(targetId)}">
              <span>${escapeHtml(label)}</span>
              <span class="module-count">${escapeHtml(count)}</span>
            </button>
          </li>
        `;
      }
```

- [ ] **Step 3: Update the workspace render call**

In `render()`, replace:

```javascript
          <section class="workspace">
            ${renderModules(items)}
```

with:

```javascript
          <section id="requirements-section-matrix" class="workspace">
            ${renderWorkbenchNavigation(items)}
```

- [ ] **Step 4: Add the quick-jump event handler**

In `attachHandlers()`, after the existing `[data-module]` handler and before the `[data-item]` handler, insert:

```javascript
        document.querySelectorAll("[data-jump-target]").forEach((button) => {
          button.addEventListener("click", (event) => {
            const targetId = event.currentTarget.dataset.jumpTarget;
            const target = targetId ? document.getElementById(targetId) : null;
            if (!target) {
              return;
            }
            target.scrollIntoView({ behavior: "smooth", block: "start" });
          });
        });
```

- [ ] **Step 5: Run focused workbench tests**

Run:

```powershell
python -m pytest docs\requirements\test_requirements_workbench.py -q
```

Expected: PASS with all tests passing.

- [ ] **Step 6: Run JSON smoke verification**

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
'@ | python -
```

Expected output:

```text
True
8
```

- [ ] **Step 7: Commit the navigation implementation**

Run:

```powershell
git add docs\requirements\requirements.html
git commit -m "feat: add requirements workbench quick navigation"
```

Expected: commit succeeds with only `docs/requirements/requirements.html` staged.

---

## Self-Review

Spec coverage:

- Task 2 adds stable IDs for mini-program, hardware, and matrix sections.
- Task 2 replaces the left sidebar title with `工作台导航`.
- Task 2 adds `快速定位` and `模块筛选`.
- Task 2 keeps module filtering on `data-module`.
- Task 2 adds independent `data-jump-target` click handling with `scrollIntoView`.
- Task 2 does not change YAML and does not introduce tabs or hidden areas.

Placeholder scan:

- The plan contains no unresolved placeholders, deferred tasks, or incomplete code blocks.

Type consistency:

- `renderWorkbenchNavigation(items)` is defined before `render()` calls it.
- `renderQuickJumpButton(targetId, label, count)` is defined immediately after `renderWorkbenchNavigation`.
- `data-jump-target` maps to `dataset.jumpTarget`, matching HTML dataset naming rules.
