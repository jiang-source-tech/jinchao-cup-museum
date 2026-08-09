# 模型服务商配置

对于 4 核 8 GB、无 GPU 的服务器，小芯第一阶段最快的实用方案是云端流式模型链路。

## 推荐快速链路

```yaml
selected_module:
  VAD: SileroVAD
  ASR: AliyunBLStreamASR
  LLM: AliLLM
  VLLM: QwenVLVLLM
  TTS: AliBLTTS
  Memory: nomem
  Intent: function_call
```

## 为什么这样配

- `AliyunBLStreamASR`：流式 ASR 不必等完整音频上传后再识别。
- `AliLLM`：首跑优先使用 `qwen-flash`。语音交互更看重低延迟，不是最大推理深度。
- `AliBLTTS`：流式 CosyVoice TTS 可以更早开始出声。
- `Memory: nomem`：记忆摘要会增加额外模型调用，先关闭，等基线足够快再加。
- `Intent: function_call`：小芯需要天气、音乐、设备控制或插件时保留这个模式。

## 最低延迟聊天模式

如果第一次只测基础语音聊天，不需要工具或插件，可以临时使用：

```yaml
selected_module:
  Intent: nointent
```

当插件、IoT 或设备行为开始重要时，再切回：

```yaml
selected_module:
  Intent: function_call
```

## 建议配置

```yaml
ASR:
  AliyunBLStreamASR:
    type: aliyunbl_stream
    api_key: YOUR_BAILIAN_API_KEY
    model: paraformer-realtime-v2

LLM:
  AliLLM:
    type: openai
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model_name: qwen-flash
    api_key: YOUR_BAILIAN_API_KEY
    temperature: 0.7
    max_tokens: 200

VLLM:
  QwenVLVLLM:
    type: openai
    model_name: qwen3.5-flash
    url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key: YOUR_BAILIAN_API_KEY

TTS:
  AliBLTTS:
    type: alibl_stream
    api_key: YOUR_BAILIAN_API_KEY
    model: cosyvoice-v2
    voice: longcheng_v2
    output_dir: tmp/
```

## 不要一开始优化这些

首跑阶段不要优先上本地 Ollama、FishSpeech、Index-TTS、PaddleSpeech 或完整本地 ASR。

没有 GPU 时，这些路线更可能增加延迟和运维复杂度。

## 调参规则

- 语音对话的 `max_tokens` 先控制在 `150` 到 `300`。
- 基线没稳定前，人格 prompt 保持短。
- 只启用小芯真正需要的插件。
- 首次延迟可接受后，再加入记忆和知识库。
