# 小芯语音天气

## 数据来源

语音天气通过原生 Python 工具 `get_xiaoxin_weather` 查询天气。该工具复用小程序 Overview 服务已经构造的天气 Provider，因此受控部署中的语音和小程序都使用同一套高德天气配置：

- `xiaoxin_control.overview_mqtt.weather_provider: amap`
- `xiaoxin_control.overview_mqtt.amap_api_key`
- `xiaoxin_control.overview_mqtt.amap_api_host`
- `xiaoxin_control.overview_mqtt.amap_city_adcodes`

环境变量 `XIAOXIN_AMAP_API_KEY` 和 `XIAOXIN_AMAP_API_HOST` 仍会覆盖 YAML 中的对应配置。

## 工具调用

`get_xiaoxin_weather` 接收以下参数：

- `city`：必填，中国城市中文名，例如 `杭州`。
- `province`：可选，省级行政区中文名，例如 `浙江`。
- `date`：可选，格式为 `YYYY-MM-DD`；默认查询今天，仅支持今天至未来三天。

模型必须直接传入中文地名。用户没有说明城市时，模型使用系统上下文中的设备位置；设备位置未知时先询问城市。工具失败时模型必须明确说明天气数据暂时不可用，不得根据模型知识猜测。

## 部署条件

语音天气不再依赖 Open-Meteo MCP、Node.js、npm 或 `npx`。`data/.mcp_server_settings.json` 保留空的 `mcpServers` 对象，供其他 MCP 工具以后按需配置。

## 缓存与回源

语音天气与小程序通过 `OverviewSyncService.query_daily_weather` 共用服务端天气缓存和 Provider：

1. 优先读取 `daily_city_weather` 中尚未过期的同城市、日期和 Provider 数据。
2. 省份为空且查询城市与设备城市一致时，使用设备位置补全省份，从而命中小程序已经写入的缓存。
3. 缓存缺失或过期时调用 Provider，校验结果后写回缓存。
4. 相同查询并发到达时只允许一个请求回源，其余请求等待缓存写入。
5. 查询其他城市时不会复用设备城市缓存。

缓存位于服务端，不依赖小程序页面是否已经打开。Provider 或缓存写入失败时不产生天气缓存，语音端只返回天气数据暂时不可用。
