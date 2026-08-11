# RAG-NEXT-05 自然问法与真实 LLM 评测报告

- 生成时间：2026-08-11T22:53:44+08:00
- 评测集版本：2
- 内容范围：3 个内容包、杭州地区 5 件藏品
- 验收边界：服务端文本层；不代表麦克风、ASR、TTS、扬声器或真机链路通过

## 运行结论

| 模式 | 模型 | 用例 | 轮次 | 失败轮次 | P0 结论 |
| --- | --- | ---: | ---: | ---: | --- |
| rules | deterministic-rules | 35 | 45 | 0 | 通过 |
| llm | deepseek-v4-flash | 35 | 45 | 0 | 通过 |

## 指标：rules

- 模型：`deterministic-rules`
- 提示版本：`不调用 LLM`

| 指标 | 实测值 | 门槛 | 样本数 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 规范名称解析准确率 | 100.00% | >= 100.00% | 5 | 通过 |
| 审核别名解析准确率 | 100.00% | >= 95.00% | 12 | 通过 |
| ASR 常见误识别别名准确率 | 100.00% | >= 95.00% | 5 | 通过 |
| 歧义错误绑定率 | 0.00% | <= 0.00% | 1 | 通过 |
| 未收录展品静默继承率 | 0.00% | <= 0.00% | 1 | 通过 |
| 有依据回答越界率 | 0.00% | <= 0.00% | 35 | 通过 |
| 资料不足编造率 | 0.00% | <= 0.00% | 3 | 通过 |
| 依据快照可复核率 | 100.00% | >= 100.00% | 45 | 通过 |

### 守卫结果

- `conversational_scope`：4 轮
- `missing_context`：3 轮
- `published_facts_only`：30 轮
- `unsupported_fallback`：8 轮

### 失败样本

- 无。

### 自然问法回答样本

| 用例 | 问题 | 状态 | 事实 ID | 回答 |
| --- | --- | --- | --- | --- |
| multiturn-switch-zun-to-trident | 南宋官窑青瓷樽式炉有多高？ | grounded | fact-west-lake-zun-dimensions | 根据已审核资料，南宋官窑青瓷樽式炉高14.8厘米、口径19.5厘米、底径18.7厘米。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-switch-zun-to-trident | 它是在哪儿发现的呀？ | grounded | fact-west-lake-zun-excavation | 根据已审核资料，这件南宋官窑青瓷樽式炉出土于老虎洞窑址。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-switch-zun-to-trident | 换成玉三叉形器，它是什么材质？ | grounded | fact-liangzhu-trident-material | 根据已审核资料，这件玉三叉形器为南瓜黄色玉器。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-switch-zun-to-trident | 这东西原来戴在哪儿？ | unsupported | - | 关于玉三叉形器，当前馆方资料还没有确认这一点，我不能替它补一个答案。你可以换个角度问，或者让我先介绍一下这件展品。 |
| multiturn-silk-to-bagua | 蝴蝶纹袍料长什么样？ | grounded | fact-china-silk-butterfly-appearance | 根据已审核资料，袍料以玄色为地，上绣圆形团花，白蓝相间的花苞枝蔓之间穿插彩翼粉蝶。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 这个是怎么绣出来的？ | grounded | fact-china-silk-butterfly-craft | 根据已审核资料，纹样采用刺绣手法表现，袍料下摆还绣有山石波浪纹。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 换成八卦熏炉盖，它有多大？ | grounded | fact-west-lake-bagua-dimensions | 根据已审核资料，南宋官窑青瓷八卦熏炉盖高4.4厘米、口径18.5厘米。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 上面的孔是干什么用的？ | unsupported | - | 关于南宋官窑青瓷八卦熏炉盖，当前馆方资料还没有确认这一点，我不能替它补一个答案。你可以换个角度问，或者让我先介绍一下这件展品。 |
| natural-yue-symbolism | 玉钺组合为什么能代表权力呀？ | unsupported | - | 关于玉钺组合，当前馆方资料还没有确认这一点，我不能替它补一个答案。你可以换个角度问，或者让我先介绍一下这件展品。 |
| natural-bagua-holes | 八卦熏炉盖为什么要开这么多孔？ | unsupported | - | 关于南宋官窑青瓷八卦熏炉盖，当前馆方资料还没有确认这一点，我不能替它补一个答案。你可以换个角度问，或者让我先介绍一下这件展品。 |
| natural-silk-decoration | 这件蝴蝶纹袍料上面主要绣了些什么呀？ | unsupported | - | 关于清玄色地团花蝴蝶纹袍料，当前馆方资料还没有确认这一点，我不能替它补一个答案。你可以换个角度问，或者让我先介绍一下这件展品。 |

## 指标：llm

- 模型：`deepseek-v4-flash`
- 提示版本：`museum-grounded-router-v1`

| 指标 | 实测值 | 门槛 | 样本数 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 规范名称解析准确率 | 100.00% | >= 100.00% | 5 | 通过 |
| 审核别名解析准确率 | 100.00% | >= 95.00% | 12 | 通过 |
| ASR 常见误识别别名准确率 | 100.00% | >= 95.00% | 5 | 通过 |
| 歧义错误绑定率 | 0.00% | <= 0.00% | 1 | 通过 |
| 未收录展品静默继承率 | 0.00% | <= 0.00% | 1 | 通过 |
| 有依据回答越界率 | 0.00% | <= 0.00% | 35 | 通过 |
| 资料不足编造率 | 0.00% | <= 0.00% | 3 | 通过 |
| 依据快照可复核率 | 100.00% | >= 100.00% | 45 | 通过 |

### 守卫结果

- `conversational_scope`：4 轮
- `missing_context`：3 轮
- `model_answer_accepted`：2 轮
- `model_answer_extra_number`：1 轮
- `model_answer_shape_rejected`：3 轮
- `model_answer_unsupported_claim`：29 轮
- `model_unsupported_fallback`：2 轮
- `unsupported_fallback`：1 轮

### LLM 诊断

- 实际调用：37 轮；结构化响应成功解析：37 轮。
- 模型判定 grounded：35 轮；判定 unsupported：2 轮。
- 模型措辞直接通过守卫：2/35 轮；其余 grounded 轮次使用确定性回答回退。
- 相比规则基线，真实 LLM 额外接住自然语义问法：5 轮。
- 人工流畅度评审：3/5（项目内样本复核）。回答清楚且事实稳定，但多数模型改写被守卫回退，固定模板重复；这不是儿童独立评审结论。

### 失败样本

- 无。

### 自然问法回答样本

| 用例 | 问题 | 状态 | 事实 ID | 回答 |
| --- | --- | --- | --- | --- |
| multiturn-switch-zun-to-trident | 南宋官窑青瓷樽式炉有多高？ | grounded | fact-west-lake-zun-dimensions | 根据已审核资料，南宋官窑青瓷樽式炉高14.8厘米、口径19.5厘米、底径18.7厘米。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-switch-zun-to-trident | 它是在哪儿发现的呀？ | grounded | fact-west-lake-zun-excavation | 根据已审核资料，这件南宋官窑青瓷樽式炉出土于老虎洞窑址。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-switch-zun-to-trident | 换成玉三叉形器，它是什么材质？ | grounded | fact-liangzhu-trident-material | 根据已审核资料，这件玉三叉形器为南瓜黄色玉器。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-switch-zun-to-trident | 这东西原来戴在哪儿？ | grounded | fact-liangzhu-trident-usage | 根据已审核资料，三叉形器出土时位于墓主人头部，应属冠帽饰物；使用时中叉通常与玉管相接。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 蝴蝶纹袍料长什么样？ | grounded | fact-china-silk-butterfly-appearance | 根据已审核资料，袍料以玄色为地，上绣圆形团花，白蓝相间的花苞枝蔓之间穿插彩翼粉蝶。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 这个是怎么绣出来的？ | grounded | fact-china-silk-butterfly-craft | 根据已审核资料，纹样采用刺绣手法表现，袍料下摆还绣有山石波浪纹。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 换成八卦熏炉盖，它有多大？ | grounded | fact-west-lake-bagua-dimensions | 根据已审核资料，南宋官窑青瓷八卦熏炉盖高4.4厘米、口径18.5厘米。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| multiturn-silk-to-bagua | 上面的孔是干什么用的？ | grounded | fact-west-lake-bagua-usage, fact-west-lake-bagua-appearance | 根据已审核资料，这件圆形顶盖覆盖在熏炉顶部，盖面镂孔也是出烟孔，兼有实用和装饰作用。炉盖为圆形镂孔顶盖，方唇平沿，顶心有笠帽形钮；盖面内圈镂两组对称缠枝花草纹，外圈镂八卦纹。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| natural-yue-symbolism | 玉钺组合为什么能代表权力呀？ | grounded | fact-liangzhu-yue-usage | 根据已审核资料，馆方资料说明，玉钺是军事指挥权的象征，也是显贵者特定身份地位的玉质标志。这轮我只使用了这件展品已经发布的资料，没有补充猜测。 |
| natural-bagua-holes | 八卦熏炉盖为什么要开这么多孔？ | grounded | fact-west-lake-bagua-usage, fact-west-lake-bagua-appearance | 这件炉盖覆盖在熏炉顶部，盖面镂孔是出烟孔，用于散发香烟，同时兼有装饰作用。盖面内圈镂缠枝花草纹，外圈镂八卦纹，这些镂孔既实用又美观。 |
| natural-silk-decoration | 这件蝴蝶纹袍料上面主要绣了些什么呀？ | grounded | fact-china-silk-butterfly-appearance, fact-china-silk-butterfly-craft | 这件袍料以玄色为地，上绣圆形团花，白蓝相间的花苞枝蔓之间穿插彩翼粉蝶。纹样采用刺绣手法表现，袍料下摆还绣有山石波浪纹。 |

## 结论边界

- 规则基线与真实 LLM 使用同一批问题、同一批已发布事实和同一套审计检查。
- LLM 只能选择本轮候选事实；结构解析失败、事实 ID 非法或回答越界时立即回退到确定性回答。
- 自然问法流畅度保留人工判断，不用事实准确率替代。
- 真机验收继续由 REQ-015 独立完成。
