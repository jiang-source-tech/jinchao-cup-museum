# 小程序主人声纹录入

## 使用方式

小程序“我的”页面的“主人声纹”面板只允许当前微信登录主体为已绑定设备录入声纹。录音通过 `wx.uploadFile` 上传到服务端的：

```text
GET  /api/miniprogram/voiceprint
POST /api/miniprogram/voiceprint   (multipart 字段：audio)
```

小程序不接触外部声纹服务密钥。服务端会校验登录主体、设备绑定、WAV 格式和 5 MB 大小限制，统一外部 multipart 文件名为 `voiceprint.wav`，然后用稳定且不可读的 `speaker_id` 代理调用外部注册接口。重新录入会覆盖外部服务中同一个 `speaker_id` 的特征，并恢复本地归档 profile；普通识别流程不会自动恢复归档 profile。

动态识别候选只从当前设备已确认的声纹 profile 加载。候选为空、数据库读取失败或识别低于阈值时，结果保持“未知说话人”，不会回退到配置文件中的静态测试人物，也不会读写主人的私人长期记忆。

## 服务成本与部署

当前适配的是开源项目 [xinnan-tech/voiceprint-api](https://github.com/xinnan-tech/voiceprint-api) 的接口。该项目使用 Apache-2.0 许可证，源码本身不要求购买授权；但必须自行承担运行它所需的服务器、数据库、容器/部署、存储和声纹推理算力成本。若改接第三方托管 SaaS，则以该供应商的调用量、存储和并发定价为准，本项目不会替其承担费用。

服务端配置示例：

```yaml
voiceprint:
  url: "http://voiceprint-host:8005/voiceprint/health?key=replace-me"
  speakers: []
  similarity_threshold: 0.2
```

`0.2` 与当前固定版本 `voiceprint-api` 的默认门槛一致，避免上游已经接受的匹配又被 Xiaoxin 适配层以更高的隐式默认值拒绝。部署仍可显式提高门槛，但必须依据真实设备采集的同人和异人分数分布标定，不能仅凭单次样本调整。

状态接口同时返回 `configured`（URL 与密钥格式已配置）和 `available`（health 接口实际返回 `status: healthy`）。`url` 为空或健康检查失败时，小程序显示“服务未配置/暂不可用”，录入按钮保持禁用。真实部署还需要完成服务启动、数据库初始化、微信小程序合法域名配置和真实设备误识别/拒识率校准；自动化测试不等于真实声纹识别已经验收。
