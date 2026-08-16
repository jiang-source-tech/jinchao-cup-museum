# 博物馆内容包合同 v2

## 1. 用途

内容包用于在不修改 Python 代码的情况下，将博物馆、展区、展品、别名、资料来源、内容版本和事实导入 SQLite。V1 继续兼容已有内容；2026 年 8 月 12 日之后新增的真实馆藏批次使用 V2。两种内容包都只允许事务化导入 `draft`；审核、发布、撤回和回滚继续由独立生命周期命令执行，不能通过文件字段绕过发布门。

对应实现：

- `main/xiaozhi-server/core/museum/content_import.py`
- `main/xiaozhi-server/scripts/import_museum_content.py`
- `main/xiaozhi-server/tests/test_museum_content_import.py`

## 2. 文件格式

支持：

- UTF-8 YAML：`.yaml`、`.yml`
- UTF-8 JSON：`.json`

根对象必须包含且只能包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | integer | 已有内容允许 `1`；新增内容使用 `2` |
| `museum` | object | 本批次所属博物馆 |
| `zones` | array | 本批次使用的展区 |
| `sources` | array | 本批次事实引用的来源 |
| `exhibits` | array | 需要导入的展品及其草稿版本 |

自动化示例位于 `main/xiaozhi-server/tests/fixtures/museum_content/valid-content.yaml`。根据杭州馆方官网整理、可进入实际内容流程的 3 馆 5 藏品内容包位于 `main/xiaozhi-server/content/museum/`。

## 3. 标识符规则

博物馆、展区、来源、展品、revision 和 fact 的 ID 使用同一规则：允许小写英文字母、数字、点、下划线和连字符，并且必须以小写字母或数字开头。

```text
hangzhou-museum
warring-states-crystal-cup
warring-states-crystal-cup-r1
fact-crystal-cup-material
```

ID 是持久化和审计标识，不应使用会频繁变化的展示文案。

## 4. 数据结构

### 4.1 museum

```yaml
museum:
  id: fixture-museum
  name: 自动化测试博物馆
  status: active
```

`status` 只允许 `active` 或 `archived`。

### 4.2 zones

```yaml
zones:
  - id: fixture-gallery
    name: 自动化测试展区
    sort_order: 1
```

每个展品引用的 `zone_id` 必须存在于同一个内容包中。展区自动绑定到根对象中的博物馆。

### 4.3 sources

```yaml
sources:
  - id: fixture-source-bronze
    title: 测试铜铃资料
    source_type: test_fixture
    locator: fixture://bronze-bell
    rights_note: 自动化测试专用，不用于游客回答。
```

V2 在上述字段之外增加：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `publisher` | 是 | 发布机构或出版者 |
| `published_date` | 否 | 已知时填写 `YYYY-MM-DD` |
| `accessed_at` | 是 | 项目核对来源的日期，格式 `YYYY-MM-DD` |
| `language` | 否 | 默认 `zh-CN` |

`locator` 可以是 URL、馆方档案号或其他稳定定位符；导入器不抓取外部内容，只保存来源元数据。

### 4.4 exhibits

```yaml
exhibits:
  - id: fixture-bronze-bell
    zone_id: fixture-gallery
    name: 测试青铜铃
    aliases: [测试铜铃]
    status: active
    image_uri: null
    revision:
      id: fixture-bronze-bell-r1
      number: 1
      status: draft
      facts: []
```

V1 的 `aliases` 仍是字符串数组。V2 使用审核对象：

```yaml
aliases:
  - text: 测试铜铃
    kind: common
    binding: unique
    sources: [fixture-source-bronze]
  - text: 铜铃
    kind: abbreviation
    binding: ambiguous
    sources: [fixture-source-bronze]
```

`kind` 允许 `common`、`abbreviation`、`asr_variant`、`historical`。`binding=unique` 才进入自动展品绑定；`binding=ambiguous` 只存档，不允许解析器静默选择展品。V2 每个别名至少绑定一个来源。

展品 `status` 只允许 `active` 或 `archived`。即使展品标记为 `active`，只要它没有 `published` revision，就不会进入游客侧 `ExhibitResolver` 索引。

### 4.5 revision

```yaml
revision:
  id: fixture-bronze-bell-r1
  number: 1
  status: draft
  facts: []
```

v1 导入命令只允许 `draft`。`reviewed`、`published` 和 `withdrawn` 是数据库已有状态，但必须由后续审核发布命令变更，不能通过导入文件绕过发布校验。

### 4.6 facts

```yaml
facts:
  - id: fixture-fact-bronze-material
    type: material
    statement: 这是一条用于验证青铜材质检索的测试事实。
    keywords: [材质, 青铜]
    confidence: test_fixture
    certainty: confirmed
    sources: [fixture-source-bronze]
```

允许的事实类型：

```text
appearance
craft
dimensions
era
excavation
history
material
observation
price
research_limit
usage
```

每条事实必须满足：

- `statement` 非空；
- `keywords` 至少一项；
- `sources` 至少一项；
- V2 的 `certainty` 必填，只允许 `confirmed`、`qualified`、`disputed`、`unknown`；
- 每个来源 ID 都存在于同一内容包的 `sources` 中；
- fact ID 在整个内容包中唯一。

## 5. 别名与名称冲突

导入器使用与当前展品解析相同的规范化规则：先执行 Unicode NFKC 和大小写折叠，再移除 Unicode 非字母数字字符及下划线。

以下情况被拒绝：

- 同一展品的规范名称与别名规范化后相同；
- 同一展品包含重复别名；
- 内容包内两个展品共享同一规范名称或别名；
- 新内容包中的名称或别名与数据库内其他已发布或已撤回的活动展品冲突。

冲突不会自动选择某个展品，也不会交给 LLM 猜测。

## 6. 校验阶段

### 6.1 文件级完整校验

在打开写事务前检查：

- 文件编码和 YAML/JSON 语法；
- 未知字段和缺失字段；
- 类型、枚举和 ID 格式；
- 内容包内重复 ID；
- 展区引用、来源引用；
- 名称与别名冲突；
- revision 必须为 `draft`。

校验会尽量聚合多个错误一次返回，不要求内容人员逐个修复再重跑。

### 6.2 数据库兼容校验

在同一 SQLite 事务中、写入任何业务行之前检查：

- 同 ID 博物馆、展区、来源和展品的元数据必须一致；
- revision ID 和同展品 revision number 不得重复；
- fact ID 不得与现有事实重复；
- 名称和别名不得与其他已发布或已撤回的活动展品冲突。

已存在且内容完全一致的博物馆、展区、来源和展品可以复用。现有展品的名称、别名和归属不能借草稿导入静默修改。

## 7. 事务与可见性

一次 `import` 对应一个 SQLite 事务，写入顺序为：

```text
museum
  -> zone
  -> source_document
    -> exhibit
  -> exhibit_alias
  -> content_revision(draft)
  -> exhibit_fact
  -> fact_source
  -> exhibit_fact_fts
```

任一步发生异常，整个批次回滚。不会留下半个展品、孤立事实或不完整 FTS 行。

草稿导入后的业务可见性：

- 内容人员可以在数据库中检查草稿；
- FTS 行已准备好，后续发布不需要重新解析原文件；
- FTS 行显式保存 `exhibit_id` 和 `revision_id`，检索在 SQL 层同时限定当前展品与当前发布版本；
- 旧数据库的 FTS 表缺少展品或版本字段时，会从关系事实表自动重建，不沿用无法证明版本边界的旧索引；
- 游客侧不会读取从未发布的纯草稿展品；曾发布后撤回的展品仍可被识别，但事实检索会返回资料不足；
- `retrieve_evidence()` 仍只读取当前展品的 `published` revision；
- 因此草稿不会通过展品解析或事实检索泄漏给游客。

## 8. CLI

### 8.1 只校验文件

```powershell
cd main/xiaozhi-server
python scripts/import_museum_content.py validate `
  --input tests/fixtures/museum_content/valid-content.yaml
```

### 8.2 同时检查数据库冲突

```powershell
python scripts/import_museum_content.py validate `
  --input tests/fixtures/museum_content/valid-content.yaml `
  --database data/museum.db
```

### 8.3 导入草稿

```powershell
python scripts/import_museum_content.py import `
  --input tests/fixtures/museum_content/valid-content.yaml `
  --database data/museum.db
```

### 8.4 审核草稿

```powershell
python scripts/import_museum_content.py review `
  --database data/museum.db `
  --revision-id fixture-bronze-bell-r1 `
  --actor reviewer-id
```

只有 `draft` 可以进入 `reviewed`。审核操作同时保存审核人、审核时间和一条 `review` 生命周期事件。可用 `--occurred-at` 传入带时区的 ISO 8601 时间；未传时使用当前 UTC 时间。

### 8.5 发布版本

```powershell
python scripts/import_museum_content.py publish `
  --database data/museum.db `
  --revision-id fixture-bronze-bell-r1 `
  --actor publisher-id
```

发布前重新检查：

- revision 状态必须为 `reviewed`；
- 审核人和审核时间非空；
- revision 至少包含一条非空事实；
- 每条事实至少绑定一个来源；
- 展品和博物馆处于 `active`；
- 规范名称和别名不与其他已发布或已撤回活动展品冲突。

发布成功时，同一展品原 `published` 版本在相同事务中变为 `withdrawn`，随后目标版本变为 `published`。任一步失败时，旧发布版本和生命周期事件全部保持不变。

### 8.6 撤回与回滚

```powershell
python scripts/import_museum_content.py withdraw `
  --database data/museum.db `
  --revision-id fixture-bronze-bell-r1 `
  --actor operator-id `
  --reason "来源需要重新确认"

python scripts/import_museum_content.py rollback `
  --database data/museum.db `
  --revision-id fixture-bronze-bell-r1 `
  --actor operator-id `
  --reason "恢复上一稳定版本"
```

`withdraw` 只接受当前 `published` 版本。撤回后展品名称仍可被识别，但新回答不能读取该版本事实。`rollback` 只接受 `withdrawn` 版本，并在同一事务中撤回当前发布版本、恢复目标旧版本。revision、fact、source 和旧 interaction trace 均不删除。

### 8.7 查看版本差异

```powershell
python scripts/import_museum_content.py show `
  --database data/museum.db `
  --exhibit-id fixture-bronze-bell
```

输出当前发布版本、各 revision 状态、审核与发布时间、事实和来源数量、相对上一版本新增/删除的 fact ID，以及审核、发布、自动替代、撤回和回滚事件。

### 8.8 复核历史回答

```powershell
python scripts/import_museum_content.py audit `
  --database data/museum.db `
  --request-id request-id-from-interaction-trace
```

`audit` 从历史 `interaction_trace.evidence_json` 读取当时使用的 `content_revision_id`、版本号、fact ID 和 source ID，再从保留的旧版本数据中还原事实陈述和来源元数据。版本、事实、来源或关联缺失时返回明确的审计失败，而不是使用当前发布版本替代。

增加 `--json` 可输出单行结构化结果，供脚本或 CI 使用。成功结果写入标准输出；失败结果写入标准错误，并包含 `status`、`error`、`message` 和 `issues`。校验或数据库冲突时进程返回码为 `2`，成功返回 `0`。

### 8.9 批量预检

```powershell
python scripts/audit_museum_content_batch.py `
  --directory content/museum `
  --json
```

该命令把目录内内容包作为一个发布候选集合，在临时隔离数据库中顺序导入，检查跨包 ID、来源、展区、revision、事实及唯一别名冲突，并输出馆、展品、事实、来源、V1/V2、唯一/歧义别名和确定性等级统计。它不会连接或修改生产数据库。

## 9. 生命周期审计

`content_revision_event` 以追加事件记录内容状态变化：

```text
review
publish
supersede
withdraw
rollback
```

每条事件保存 revision、展品、原状态、目标状态、执行人、原因和发生时间。状态变更和事件写入使用同一 SQLite 事务，不允许出现“状态已变化但没有事件”或“事件存在但状态未变化”。

## 10. 兼容与限制

- V1 导入时会把字符串别名双写到 `exhibit_alias`，类型默认为 `common`、绑定方式默认为 `unique`、来源为空；已有数据库启动时执行相同回填。
- V2 来源元数据、别名对象和事实确定性写入结构化字段；`aliases_json` 暂时保留，只包含可自动绑定的 `unique` 别名，以兼容现有检索和解析代码。
- 现有 V1 内容不会因为缺少 V2 元数据而被自动伪造为完整 V2；批量报告会明确列出 schema 版本数量。
- 内容管理后台；
- 持久化知识 release 激活表和事务化数据库/向量双系统发布。

V2 合同曾作为扩展到百件内容的前置条件；当前仓库候选内容已经达到 101 件展品、187 条事实和 108 个来源，但这不代表来源已经由真实馆方人工复核，也不代表当前新增内容已进入生产。
