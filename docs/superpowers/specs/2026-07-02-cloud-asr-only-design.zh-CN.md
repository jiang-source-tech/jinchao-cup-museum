# 仅保留云端 ASR 设计

## 目标

小芯不再支持本地或自托管 ASR 路径。运行时只保留云端/API ASR 供应商，让部署不再依赖本地语音识别模型、FunASR 服务、Sherpa 模型或 Vosk 模型。

## 范围

移除 active runtime 中的这些本地或自托管 ASR 路径：

- 本地 FunASR：`FunASR`、`fun_local.py`、`models/SenseVoiceSmall`。
- 自托管 FunASR 服务：`FunASRServer`、`fun_server.py`。
- 本地 Sherpa-ONNX ASR：`SherpaASR`、`SherpaParaformerASR`、`sherpa_onnx_local.py`。
- 本地 Vosk ASR：`VoskASR`、`vosk.py`。
- 仅为上述路径存在的 Python 依赖。
- 需要 `models/SenseVoiceSmall/model.pt` 的 Docker compose 挂载。
- active Xiaoxin 文档中要求用户创建或保留 `models/SenseVoiceSmall` 的说明。

保留：

- 阿里云、阿里云流式、百炼流式、Qwen3 ASR Flash、讯飞流式、豆包、腾讯、百度和 OpenAI 兼容 ASR 等云端/API 供应商。
- 本地 VAD 与 `models/snakers4_silero-vad`，因为 VAD 不是 ASR，语音链路仍然需要它。
- `docs/upstream-archive/` 下的上游归档文档，因为它们只是历史参考。

## 运行时配置

默认 `selected_module.ASR` 应离开 `FunASR`。推荐默认值：

```yaml
selected_module:
  ASR: AliyunBLStreamASR
```

第一阶段快速路径仍建议：

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

被移除的 ASR 名称不应再出现在 active runtime 配置块、依赖文件或 compose 挂载中。

## 架构

ASR provider factory 已按 `type` 动态加载供应商，不需要新增抽象。本设计只是收窄供应商集合：移除本地/自托管 provider 模块和配置项，保留云端 provider 动态加载路径。

语音链路、VAD、LLM、意图、记忆和 TTS 不需要行为级改造。

## 文档

active Xiaoxin 文档应把云端 ASR 写成首次运行路径，不再要求用户创建 `models/SenseVoiceSmall`。上游归档文档可以继续保留本地 ASR 说明。

## 验证

- 搜索 active runtime 和 active docs，确认不再出现被移除路径作为当前建议。
- 确认保留的云端 ASR provider 文件仍存在。
- 确认 `config.yaml` 可以解析。
- 确认默认 ASR 指向保留的云端 provider。
- 运行不需要真实 API Key 的轻量级导入或配置检查。

## 不做

- 不移除云端 ASR provider。
- 不移除 VAD 模型或 VAD 依赖。
- 不编辑上游归档文档，除非它们影响 active tooling。
- 不在本次变更中配置真实 API Key。
