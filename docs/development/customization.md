# 定制开发说明

先让默认项目跑通完整语音链路，再开始定制小芯。不要一边排部署问题，一边改人格、音色、插件和运行路径；那会让问题来源变得不可判断。

## 推荐顺序

1. 跑通默认 Docker 单服务部署。
2. 配置低延迟云端模型链路。
3. 确认 ESP32 可以唤醒、连接、聆听、回答和播报。
4. 每次只改一个定制点。
5. 每改一个点，都做一次真机或本地 smoke 验证。

## 人格

默认人格主要来自：

```text
main/xiaozhi-server/config.yaml
main/xiaozhi-server/agent-base-prompt.txt
```


Console accounts, devices and delivery data use local SQLite; persona and model settings are maintained in local configuration files.
控制台用于账号、设备绑定、通知和运行状态管理；人格与模型配置通过本地配置文件维护。

## 音色

TTS provider 和音色通过模型设置配置。

小芯第一条快速路径建议使用：

```text
TTS provider: AliBLTTS
voice: 已确认可用的 CosyVoice 音色
```

只有当基线链路能稳定播报后，才更换音色。否则无法判断问题来自 TTS、网络、模型参数还是音频播放。

## 唤醒词

唤醒词在 `main/xiaozhi-server/config.yaml` 的 `wakeup_words` 下配置。

设备连接稳定前保留默认列表。连接和语音链路稳定后，再替换或增加小芯专属唤醒词，例如“小新”“晓新”。

## 模型链路

第一阶段推荐链路：

```yaml
ASR: AliyunBLStreamASR
LLM: AliLLM
VLLM: QwenVLVLLM
TTS: AliBLTTS
Memory: nomem
Intent: function_call
```

如果只是测试纯聊天延迟，可以临时使用：

```yaml
Intent: nointent
```

需要插件、工具、IoT 或设备控制时，再切回：

```yaml
Intent: function_call
```

## 插件

插件目录：

```text
main/xiaozhi-server/plugins_func/functions
```

只启用产品真正需要的插件。每多一个插件，都会增加 prompt 复杂度、配置要求和失败模式。

插件上线前至少确认：

- 插件参数是否稳定。
- 插件失败时是否有清晰回复。
- 插件是否会拖慢首字响应。
- 插件返回内容是否适合语音播报。

## 品牌和文案

早期品牌定制优先改用户能看到的内容：

- `README.md`
- `docs/README.md`
- Xiaoxin 控制台标签。
- 默认人格 prompt。
- 小芯控制台文案。

部署稳定前不要急着改包名、数据库名和目录名。继承名称可以暂时保留，避免增加无意义的迁移成本。
