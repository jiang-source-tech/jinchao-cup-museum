# 需求追踪与交付看板

本目录提供一套轻量级、可版本控制的需求管理机制：`requirements.yaml` 是唯一事实来源，`render_requirements.py` 校验数据并生成可离线打开的 `index.html`。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `requirements.yaml` | 需求编号、顺序、优先级、状态、验收条件、依赖与证据 |
| `requirements.schema.json` | YAML 数据结构的 JSON Schema 描述 |
| `render_requirements.py` | 结构校验、状态一致性检查和 HTML 生成 |
| `template.html` | 看板布局、样式和筛选交互 |
| `index.html` | 自动生成的离线看板，不应手工编辑 |
| `test_render_requirements.py` | 数据与生成结果回归测试 |
| `ac-010-4-firmware-verification.md` | 固件协议测试、目标板构建结果和真机验收边界 |
| `rag-execution-plan.md` | REQ-013 之后的事实级 RAG 逐项实施、验收和决策门 |

## 更新流程

1. 修改 `requirements.yaml`。
2. 为完成条件补充代码、测试、文档或真机记录证据。
3. 运行校验和生成：

```powershell
python docs/requirements/render_requirements.py
```

4. 验证生成文件没有漂移：

```powershell
python docs/requirements/render_requirements.py --check
python -m pytest -q docs/requirements/test_render_requirements.py
```

5. 在浏览器中打开 `docs/requirements/index.html`。

## 状态规则

- `done`：所有验收条件都有完成证据。
- `in_progress`：至少一项完成，仍有待完成条件。
- `pending`：尚未开始，没有完成条件。
- `blocked`：至少一项验收条件被明确阻塞。
- `deferred`：已经有意后置，不参与当前主线交付。

完成度由验收条件中 `done` 的数量自动计算。不要在 YAML 中手工维护百分比，也不要把自动化测试通过写成真机验收通过。
