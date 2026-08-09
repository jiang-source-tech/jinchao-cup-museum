# Xiaoxin 需求工作台导航优化设计

日期：2026-07-06

## 背景

`docs/requirements/requirements.html` 当前已经包含三类核心阅读区域：

- 学生侧小程序需求分栏。
- 硬件端需求分栏。
- 下方状态矩阵与模块筛选。

但左侧栏仍然叫“模块导航”，并且只负责过滤状态矩阵中的 `module`。这会造成一个结构错位：用户最想快速定位的三个区域不在同一个导航体系里，硬件端和小程序端虽然已经成为一等需求块，却不能从左侧导航快速进入。

## 目标

把左侧栏从“模块导航”升级为“工作台导航”，让用户可以快速定位三个核心区域：

- 小程序需求。
- 硬件端需求。
- 服务端状态矩阵。

同时保留现有模块筛选能力，避免失去按模块过滤 `items` 的效率。

## 非目标

本设计不改 `requirements.yaml` 的数据结构。

本设计不引入 tab 切换，不隐藏任何区域。需求工作台仍然是一页总账。

本设计不做滚动监听自动高亮。第一版只做点击后的显式定位和可见导航结构，避免为了一个轻量导航引入复杂状态。

## 方案选择

采用“工作台导航 + 模块筛选”的双段式左栏。

结构如下：

```text
工作台导航
  快速定位
    小程序需求
    硬件端需求
    服务端状态矩阵

  模块筛选
    全部模块
    服务端
      服务端语音链路
      OTA 与 WebSocket 私有路径
      Xiaoxin runtime 与分层记忆
    固件端
      宠物主页
      通知中心
      Overview 总览页
```

理由：

- “快速定位”解决去哪看。
- “模块筛选”解决怎么筛。
- 两个动作不同，不应该混在一个“模块导航”标题下。
- 保留一页总账，不让用户在 tab 间丢失上下文。

## 交互

### 快速定位

新增三个按钮：

- `小程序需求`：滚动到 `mini_program_requirements` 渲染区块。
- `硬件端需求`：滚动到 `hardware_requirements` 渲染区块。
- `服务端状态矩阵`：滚动到下方包含模块导航、状态矩阵和详情栏的 `workspace` 区块。

点击快速定位不改变搜索、状态、优先级、模块等筛选条件。

### 模块筛选

现有 `全部模块` 和各模块按钮保留，行为不变：

- 点击 `全部模块` 设置 `state.filters.module = "all"`。
- 点击某个模块设置 `state.filters.module = module.id`。
- 状态矩阵按筛选结果刷新。

### 高亮

第一版只保留模块筛选高亮。快速定位按钮可以使用普通 hover 样式，不做滚动位置自动高亮。

如果未来发现用户需要更强定位感，再增加 `state.activeWorkbenchSection` 或 IntersectionObserver。

## HTML 结构

给三个目标区域增加稳定锚点：

```html
<section id="requirements-section-mini_program_requirements" class="panel miniprogram-panel"></section>
<section id="requirements-section-hardware_requirements" class="panel miniprogram-panel"></section>
<section id="requirements-section-matrix" class="workspace"></section>
```

左栏快速定位按钮使用：

```html
<button data-jump-target="requirements-section-mini_program_requirements">小程序需求</button>
<button data-jump-target="requirements-section-hardware_requirements">硬件端需求</button>
<button data-jump-target="requirements-section-matrix">服务端状态矩阵</button>
```

点击处理使用 `scrollIntoView({ behavior: "smooth", block: "start" })`。如果目标元素不存在，静默忽略，不打断页面。

## 渲染影响

`renderRequirementSection(sectionKey, options)` 需要输出稳定 `id`：

```javascript
<section id="requirements-section-${sectionKey}" class="panel miniprogram-panel">
```

`render()` 中的 workspace 需要稳定 `id`：

```javascript
<section id="requirements-section-matrix" class="workspace">
```

`renderModules(items)` 需要改为 `renderWorkbenchNavigation(items)`，内部包含快速定位和模块筛选两段。模块按钮仍使用 `data-module`，快速定位按钮使用 `data-jump-target`，两者不能共用事件处理。

## 错误处理

如果某个快速定位目标不存在，点击后不报错、不改变状态。

如果 `hardware_requirements` 在旧 YAML 中缺失，对应按钮可以仍然存在但点击无效果；也可以根据数据存在性渲染。第一版推荐根据数据存在性渲染，避免导航到不存在区域。

## 测试

补充或扩展 `docs/requirements/test_requirements_workbench.py`：

- HTML 包含 `function renderWorkbenchNavigation(`。
- HTML 包含 `data-jump-target`。
- HTML 包含三个目标 id：
  - `requirements-section-mini_program_requirements`
  - `requirements-section-hardware_requirements`
  - `requirements-section-matrix`
- HTML 包含 `scrollIntoView`。
- 既有工作台测试继续通过。

## 完成标准

打开需求工作台后，左栏不再只是“模块导航”。用户可以先快速跳到小程序需求、硬件端需求或服务端状态矩阵，再在状态矩阵中按模块筛选。

最重要的判断标准是：导航语义与当前工作台结构一致，用户不会再误以为左栏只能控制下方状态矩阵。
