# 博物馆领域数据模型

## 设计目标

数据模型必须支持三个核心结果：回答可追溯、内容可审核、现场上下文可确定。第一版使用 SQLite，避免为少量展品引入外部基础设施。

## 核心实体

### museum

| 字段 | 含义 |
| --- | --- |
| `id` | 稳定标识 |
| `name` | 展馆名称 |
| `status` | `active` 或 `archived` |

### zone

| 字段 | 含义 |
| --- | --- |
| `id` | 展区标识 |
| `museum_id` | 所属展馆 |
| `name` | 展区名称 |
| `sort_order` | 展示顺序 |

### exhibit

| 字段 | 含义 |
| --- | --- |
| `id` | 展品标识 |
| `zone_id` | 所属展区 |
| `name` | 规范名称 |
| `aliases_json` | 可识别别名 |
| `image_uri` | 展品图片资源 |
| `status` | `active` 或 `archived` |

### source_document

| 字段 | 含义 |
| --- | --- |
| `id` | 来源标识 |
| `museum_id` | 所属展馆 |
| `title` | 资料名称 |
| `source_type` | 馆方记录、出版物、研究材料等 |
| `locator` | 页码、章节、馆藏编号或授权位置 |
| `rights_note` | 授权与使用说明 |

### content_revision

| 字段 | 含义 |
| --- | --- |
| `id` | 版本标识 |
| `exhibit_id` | 所属展品 |
| `revision_no` | 单展品递增版本号 |
| `status` | `draft`、`reviewed`、`published`、`withdrawn` |
| `reviewed_by` | 审核人标识 |
| `reviewed_at` | 审核时间 |
| `published_at` | 发布时间 |

### exhibit_fact

| 字段 | 含义 |
| --- | --- |
| `id` | 事实标识 |
| `revision_id` | 所属内容版本 |
| `fact_type` | 年代、材质、工艺、用途、背景等 |
| `statement` | 最小可审核陈述 |
| `keywords_json` | 检索关键词 |
| `confidence` | 馆方确认等级，不是模型概率 |

### fact_source

连接 `exhibit_fact` 与 `source_document`，允许一条事实有多个来源，也允许一个来源支持多条事实。

### device_placement

| 字段 | 含义 |
| --- | --- |
| `device_id` | 固件设备标识 |
| `museum_id` | 所属展馆 |
| `zone_id` | 当前展区 |
| `default_exhibit_id` | 默认展品 |
| `updated_at` | 最近配置时间 |

### visitor_session

| 字段 | 含义 |
| --- | --- |
| `id` | 临时会话标识 |
| `device_id` | 发起设备 |
| `current_exhibit_id` | 已确认当前展品 |
| `started_at` | 开始时间 |
| `expires_at` | 过期时间 |

### tour_definition

定义一条可发布路线，包括名称、预计时长、主题和状态。

### tour_stop

定义路线中的展品顺序、到站提示、观察任务和完成后的下一站。

### tour_progress

记录游客会话当前路线、当前站点和已完成站点。它属于临时会话，不属于长期用户画像。

### interaction_trace

保存问题、当前展品、依据快照、回答、守卫结果和阶段延迟。未命中问题通过 `grounding_status=unsupported` 和 `unanswered_reason` 表达，不另造一份不可关联的数据。

## 不变量

1. 一条展品事实必须绑定至少一个资料来源。
2. 每件展品同一时间最多有一个发布版本。
3. 草稿、已撤回版本和无来源事实不得进入依据快照。
4. 有依据回答引用的事实必须全部属于本轮依据快照。
5. 当前展品必须来自权威上下文，不得由模型自行确定。
6. 回答策略不能改变检索到的事实集合；临时表达调整只能影响排序、篇幅和措辞。
7. 路线进度必须由显式操作提交，模型建议本身不改变状态。
8. 临时会话过期后不得继续被当作游客身份。
9. 内容发布和撤回必须留下审计记录。

## 第一版检索

第一版按以下顺序检索：

1. 限定 `current_exhibit_id` 和发布版本；
2. 对展品名称、别名、事实类型、陈述和关键词执行 FTS5 检索；
3. 对年代、材质、工艺等明确意图优先返回对应事实类型；
4. 最多返回能够支撑短回答的少量事实；
5. 没有可靠事实时返回 `unsupported`，不扩大到全馆自由检索。

跨展品比较只有在问题明确提到另一件已识别展品时才允许，并分别建立两个依据快照。
