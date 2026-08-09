# 小芯语音天气缓存优先设计

## 目标

语音天气与小程序天气通过同一个 `OverviewSyncService` 查询接口读取天气。查询优先复用服务端 `daily_city_weather` 中尚未过期的数据；缓存缺失或过期时才调用已配置的天气 Provider，并在成功后写回缓存。

本设计解决以下问题：

- 避免每次语音天气查询都调用高德天气。
- 保证语音与小程序在相同城市、日期和 Provider 下复用同一份服务端数据。
- 将缓存命中、过期、并发去重、回源和写回规则集中在一个模块中。
- 保留查询其他城市的能力，不依赖小程序页面是否打开。

## 非目标

- 不改变高德天气返回字段或预报天数。
- 不让语音插件直接读取 SQLite 或 Overview 快照。
- 不把小程序客户端页面状态当作天气事实源。
- 不改变现有天气缓存到期规则、每日刷新时间或失败重试调度。
- 不引入 Redis、额外数据库或新的外部天气供应商。

## 方案比较

### 方案 A：语音插件直接读取 `XiaoxinOverviewStore`

实现最少，但插件必须理解缓存键、过期规则、Provider 名称和写回时机。缓存知识会同时存在于插件与 Overview 模块中，后续修改容易产生分叉。

结论：拒绝。

### 方案 B：只读取设备的 Overview 快照

能够回答设备当前城市的今日天气，但不能可靠查询其他城市或未来日期。快照还是面向小程序展示的 Projection，不应反向充当天气查询接口。

结论：拒绝。

### 方案 C：`OverviewSyncService` 提供缓存优先查询接口

语音插件只表达查询意图，Overview 模块统一承担缓存和回源策略。小程序刷新与语音查询复用同一 Store、Provider 和缓存键。

结论：采用。

## 模块与接口

在 `OverviewSyncService` 增加异步接口：

```python
async def query_daily_weather(
    self,
    province: str,
    city: str,
    date_text: str,
    *,
    device_id: str | None = None,
    country_code: str = "CN",
) -> DailyWeather:
    ...
```

调用者只需要知道地点、日期和可选的设备标识。以下实现知识全部留在 Overview 模块内部：

- 当前天气 Provider 名称。
- `daily_city_weather` 的缓存键。
- 缓存有效期判断。
- 同一地点和日期的并发去重。
- Provider 调用和成功写回。

`DisabledOverviewSyncService` 提供同名接口并明确失败，使语音插件不需要识别具体实现类型。

## 地点补全

语音模型可能只传城市，例如 `city="杭州", province=""`。为了命中小程序已经写入的 `浙江/杭州` 缓存，查询接口按以下顺序补全地点：

1. 规范化输入中的空白和常见行政区后缀后比较地点。
2. 当省份为空且提供了 `device_id` 时，读取设备已保存的位置。
3. 如果设备位置的城市与请求城市相同，使用设备位置中的省份和国家代码。
4. 如果设备位置不存在或城市不同，保留空省份并继续查询；Provider 负责地理编码，成功结果按该查询地点缓存。
5. 调用方明确提供省份时绝不被设备位置覆盖。

这一规则使“今天的天气怎么样”复用当前设备的小程序天气，同时保证“北京天气怎么样”不会错误读取杭州缓存。

## 查询流程

```text
语音工具
  -> OverviewSyncService.query_daily_weather
      -> 补全并规范化地点
      -> 查询 daily_city_weather
          -> 命中未过期数据：直接返回
          -> 未命中：进入地点+日期+Provider 锁
              -> 再查一次缓存
                  -> 已由并发请求写入：直接返回
                  -> 仍未命中：调用天气 Provider
                      -> 校验日期和地点
                      -> 写入 daily_city_weather
                      -> 返回
```

锁使用 `OverviewSyncService` 已有的键控异步串行机制，锁键包含国家、省份、城市、日期和 Provider。锁内二次读取缓存，避免两个同时到达的语音请求重复调用高德。

## 语音工具调整

`get_xiaoxin_weather` 不再访问 `overview_service.weather_provider`，而是调用 `overview_service.query_daily_weather(...)`，并传入 `conn.device_id`。

语音工具继续负责：

- 校验日期格式和支持范围。
- 把 `DailyWeather` 格式化为供 Qwen 组织口语回复的事实文本。
- 将查询异常统一转换成“天气数据暂时不可用”，不向模型暴露内部 Provider 错误。

语音工具不再负责：

- 判断缓存是否有效。
- 调用具体 Provider。
- 写入天气缓存。
- 选择 Provider 名称。

## 数据一致性

缓存仍使用现有键：

```text
[country_code, province, city, date, provider]
```

Store 已经在读取时拒绝过期数据，并在写入时设置到期时间。本次设计不建立第二套缓存，也不读取客户端页面中的数据。

Provider 返回后必须满足：

- 日期与请求日期相同。
- 城市与补全后的请求城市相同。
- 请求明确包含省份时，省份必须相同。
- 国家代码与查询国家相同。

校验失败时不写缓存。

## 错误处理

- 缓存读取异常：查询失败，不绕过数据库悄悄请求 Provider，避免掩盖存储故障。
- Provider 异常：不写缓存，异常返回语音工具，由其生成稳定的暂不可用结果。
- 缓存写入异常：查询失败；不把未持久化结果伪装成缓存成功。
- 已有有效缓存：即使 Provider 当前不可用，也不会调用 Provider。
- 取消信号：`asyncio.CancelledError` 原样传播。

## 可观测性

`OverviewSyncService` 在调试级别记录查询来源：`cache_hit`、`cache_fill`。错误日志不得包含 API Key。现有语音工具继续记录最终查询异常，但不把内部错误交给 Qwen。

## 测试设计

### Overview 模块

- 有有效缓存时返回缓存，Provider 调用次数为零。
- 缓存缺失时调用 Provider 一次，写回后第二次查询命中缓存。
- 缓存过期时回源 Provider。
- 两个相同并发请求只调用 Provider 一次。
- Provider 失败时不写入缓存。
- 缓存写入失败时传播错误。
- 省份为空且设备城市匹配时，使用设备省份命中小程序缓存。
- 省份为空但设备城市不匹配时，不读取错误城市的缓存。
- 明确省份时保持现有跨省拒绝行为。

### 语音工具

- 调用 `query_daily_weather` 并传递 `device_id`。
- 缓存返回的数据能格式化为天气事实文本。
- 查询异常转换为稳定的暂不可用结果。
- 测试不再直接伪造 `weather_provider.daily`。

### 回归验证

- 运行语音天气、Overview Service、Store、Provider 和配置契约相关测试。
- 使用真实高德接口验证一次缓存未命中的回源路径。
- 使用临时 SQLite Store 连续查询两次，确认第二次不调用 Provider。

## 验收标准

1. 同一设备、城市、日期和 Provider 已有有效小程序天气数据时，语音查询不调用高德。
2. 缓存没有数据或已经过期时，语音查询调用高德一次并写回缓存。
3. 同键并发查询不会重复回源。
4. 查询其他城市不会错误复用设备城市缓存。
5. Provider 或缓存失败时不产生伪造天气，也不污染有效缓存。
6. 现有小程序天气刷新、跨省校验和语音天气测试继续通过。
