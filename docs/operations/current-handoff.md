# 小芯可信记忆模块｜当前交接快照

更新时间：2026-07-17 17:56（Asia/Shanghai）

本文只保存“接手后立即需要知道的当前事实”。完整实施历史、候选、验收数据和失败记录统一保存在：

`../superpowers/plans/2026-07-15-xiaoxin-memory-module-execution-ledger.md`

若本文与旧对话或旧任务描述冲突，以当前 Git object、执行台账和 requirements 为准。

## 1. 当前结论

- 服务端仓库：`D:\AI_Pet\xiaoxin-esp32-server`
- GitHub：`git@github.com:jiang-source-tech/xiaoxin-esp32-server.git`
- 当前分支：`main`
- 本轮文档整理前，本地 `main` 与 `origin/main` 均为 `0ee476bddef8fe227d434da613677ec106015ee5`；整理后的精确对象请用 `git rev-parse HEAD` 获取。
- resolve-only 产品合并提交：`5bd6c8af8829459c9f3602ffdfcc1a10dbcd1755`
- resolve-only 候选：`f0baf0a83d1990f7a015f8ed52296c9159059bb3`
- 独立结论：`QUICK_APPROVE / P0=0 / 19/19 PASS`
- main 合并后门禁：resolve `50 passed`、`tests/xiaoxin=1848 passed`、requirements `10 passed`、compileall 与 Git 门禁通过。
- 当前没有活跃后台实现任务、验收任务或自动监督。
- 当前阶段：resolve-only 快速工程集成完成，等待实机观察；完整 hardening 尚未完成。

## 2. 已经进入 main 的核心能力

- 不可变 Memory contracts、统一 `TurnAnalysis` 和 `MemoryEngine.prepare_turn -> commit_turn` seam。
- SQLite Evidence Store、schema v7、WAL、foreign keys、事务、幂等和冲突检测。
- confirmed speaker、canonical subject、alias、scope、policy 和 attribution 隔离。
- Profile Evidence、称呼纠正、active/superseded/forgotten 投影和解释能力。
- C 语言成长事件：
  - concern
  - attempt
  - progress
  - setback
  - pause
  - restart
  - milestone
  - resolve
- progress -> attempt correction、correction-unit forget、subject-scoped topic block。
- active Evidence 重建成长状态；普通 prompt 默认不注入成长记忆，明确回望使用结构化 Evidence。
- resolve-only：固定句机械识别、sealed Prepared token、target snapshot/digest、commit-time revalidation、`resolve -> resolved`、零 legacy followup。

resolve-only 固定句：

```text
我最近把 C 语言指针问题解决了。
```

只允许删除 Unicode White_Space，并允许省略唯一句末全角 `。`。关键 near-miss、额外子句、ASCII 句点、错误主体、fallback/unknown route、无 active target、topic block 和失效 target 均 fail-closed。

## 3. Task 0–13 的真实状态

这里的 Task 0–13 指改进计划中的总体任务，不等同于 S1–S13 实施切片。

| Task | 状态 | 说明 |
|---|---|---|
| 0 基线和反例合同 | 已完成 | 已有持续扩展的 parser、Runtime、Store 和零副作用测试。 |
| 1 不可变合同类型 | 已完成 | `contracts.py` 已成为核心合同。 |
| 2 统一 TurnAnalysis | 已完成主体 | 仍有少量旧关键词规则待收敛。 |
| 3 深 MemoryEngine seam | 已完成主体 | Runtime 主要通过 prepare/commit 使用可信记忆。 |
| 4 SQLite Evidence Store | 已完成主体 | schema v7 已运行，事务和幂等门禁存在。 |
| 5 Profile Evidence 投影 | 大部分完成 | 称呼纠正和生命周期已实现，完整软事实生命周期仍有债务。 |
| 6 C 语言成长状态机 | 部分完成 | 已有八类事件；reflection 和 reopen 尚未实现。 |
| 7 correction/forget/block/purge | 部分完成 | correction、定向 forget、topic block 已实现；purge 和全投影统一遗忘未完成。 |
| 8 Episode 语义重建 | 未完成 | 旧 Episode/JSON 路径仍然存在。 |
| 9 召回硬过滤与排序 | 部分完成 | Engine 安全过滤已存在；离线数据集、完整排序和 reflection 召回未闭合。 |
| 10 主体成长资格与合并 | 大部分完成 | confirmed/unknown、alias、scope 和 attribution 已有系统测试。 |
| 11 旧 JSON 数据迁移 | 未完成 | 尚无计划要求的完整 legacy importer、dry-run 和切换流程。 |
| 12 黄金故事和跨会话验收 | 部分完成 | 已有大量纵向故事测试，但完整九轮故事未整体封板。 |
| 13 删除重复实现并更新需求 | 未完成 | legacy adapter/重复路径仍在；requirements 中多个 CG 仍为 partial/todo。 |

因此不能宣称 Task 0–13 已全部完成。

## 4. 当前未完成工作

### 产品切片

1. reopen-only
2. reflection-as-event
3. purge-only

### resolve post-landing hardening debt

1. R001–R173 完整 fresh-archive 矩阵、P0/P1 和零 UNKNOWN 收口。
2. Runtime B–G、no-transcript 和 new-session rebuild 完整端到端矩阵。
3. parser、target digest、block、forgotten、legacy、empty-turn、idempotency、reducer、prompt 的系统 mutation-kill。
4. resolve 专项并发压力、事务故障注入和罕见 rollback/竞态。
5. old target 与 resolve Evidence 自身后续 forgotten/superseded 的完整 post-resolve 验收。

### 架构债务

- Task 8 Episode 语义重建。
- Task 11 legacy JSON importer 与单次迁移切换。
- Task 13 replace-not-layer、删除重复写路径并更新 requirements 状态。

## 5. 实机验证清单

1. 先让同一用户产生一条 active trusted C 语言成长事件。
2. 说固定句，确认 resolve 正常提交且回复无异常。
3. 新会话或服务重启后明确回望成长状态，确认从结构化 Evidence 重建为 `resolved`。
4. 在 no-history、topic block、forgotten/superseded/expired target 下说固定句，确认没有新增 resolve Evidence。
5. 测试 ASCII 句点、额外子句、引用和 near-miss，确认不进入 resolve。
6. 重试同一 turn，确认不产生重复 Evidence。
7. 记录实际输入、route、commit status、Evidence 数量和用户可见回复；不要只记录“感觉正常”。

## 6. 下一步决策顺序

1. 先完成上述实机 smoke。
2. 若出现数据污染、重复写、实机永远 deferred 或普通 prompt 注入，优先建立有界修复任务。
3. 若实机结果稳定，选择：
   - 优先补 resolve hardening；或
   - 启动 reopen-only 最小切片；或
   - 两者使用独立 worktree 并行。
4. reflection、purge、Episode 重建和 legacy migration 不得偷渡进 reopen-only。

## 7. 操作边界

- Git push 是外部写操作；只有用户明确授权时才执行。2026-07-17 用户已明确授权本轮文档整理后推送 `main`。
- 默认禁止 pull、rebase、amend、force push 和 destructive reset。
- 并行代码任务必须使用独立 worktree；只读审计可共享主目录。
- 不得读取、修改、删除、stash 或提交用户的 `.codex-review/`、`output/` 和真实 `data/`。
- 固件和小程序默认不在当前服务端记忆切片范围内；不得擅自修改。
- 测试使用系统 Temp 下的独立 `--basetemp`，不要使用或清理用户目录。
- 不得把“测试全绿”替代 Store 边界、事务顺序和真实 Runtime 调用链检查。

## 8. 权威资料

1. `docs/operations/current-handoff.md`：当前交接快照。
2. `docs/product/domain-language.md`：产品领域词汇表。
3. `docs/superpowers/plans/2026-07-15-xiaoxin-memory-module-decision-map.md`：决策地图。
4. `docs/superpowers/plans/2026-07-15-xiaoxin-memory-module-improvement.md`：Task 0–13 总计划。
5. `docs/superpowers/plans/2026-07-15-xiaoxin-memory-module-execution-ledger.md`：完整执行历史。
6. `docs/requirements/requirements.yaml`：产品能力状态；当前 CG-01/02/04 为 partial，CG-03/05/06/07 为 todo。

历史验收材料、候选、数据和失败记录以执行台账为准；本机临时目录不是权威资料，也不应写入长期交接入口。

## 9. 新会话恢复命令

```powershell
cd D:\AI_Pet\xiaoxin-esp32-server
git rev-parse HEAD
git rev-parse origin/main
git status --short --branch
git log -8 --oneline --decorate
python -m pytest main/xiaozhi-server/tests/xiaoxin/test_memory_growth_resolve.py -q
```

执行任何修改前，先确认当前任务是实机问题修复、resolve hardening 还是 reopen-only，不要从旧任务描述推断。

## 10. 完成定义

当前项目总计划尚未完成。至少满足以下条件后，才能宣称 Task 0–13 整体完成：

- reopen、reflection、purge 的产品合同和实现完成。
- Episode 语义完成重建。
- legacy JSON 完成可观察、幂等、可回滚的单次迁移。
- 重复旧写路径被删除或降为明确只读兼容 adapter。
- requirements 状态与真实实现一致。
- 完整黄金故事、跨会话、主体隔离、纠正、遗忘、block、resolve/reopen 和 purge 验收通过。

当前置信度：快速落地范围高；Task 0–13 总体完成度中等，不能标记为全部完成。
