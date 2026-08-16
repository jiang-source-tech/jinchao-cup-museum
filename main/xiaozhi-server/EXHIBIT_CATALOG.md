# 服务端展品与可提问范围清单

## 1. 使用说明

本清单回答三个问题：当前仓库收录了哪些展品、每件展品现有资料能支持哪些问题、哪些展品适合作为重点演示对象。

数据口径来自 2026 年 8 月 15 日对 [`content/museum/`](content/museum/) 的批量审计：

```text
内容包 8 个 / 博物馆 4 家 / 展品 101 件 / 事实 187 条 / 来源 108 个
唯一别名 67 个 / 歧义别名 14 个
```

必须区分以下三种状态：

- **仓库已收录**：展品存在于内容包中，当前内容包 revision 均为 `draft`。
- **可提问范围**：根据该展品已有事实类型判断；只有经过 `review -> publish` 的 revision 才能进入运行时回答。
- **生产已部署**：以单独的生产发布报告和服务器验收为准，不能用本清单的仓库统计替代。

当前资料是公开网页、公开文章和项目演示整理资料，不代表真实馆方授权、馆方审核或馆方正式发布。

## 2. 能力分级

| 级别 | 数量 | 判定依据 | 适合的提问 |
| --- | ---: | --- | --- |
| 重点深问 | 3 | 8 至 10 条原子事实，覆盖年代、发现、材质/工艺、用途和研究边界，并已建立章节级公开资料证据包与 `claim_support` | 详细介绍、发现经过、制作工艺、用途解释、研究争议、综合追问 |
| 常规多维 | 15 | 4 至 6 条有来源事实，能够覆盖若干核心维度，但尚未建立与重点展品同等级的原文证据包 | 年代、尺寸、材质、外观、出土、工艺或用途中的已有维度 |
| 基础登记 | 83 | 每件仅有 1 条 `history` 事实，来源是中国丝绸博物馆官网目录页 | 展品名称、是否收录、官网登记信息；不适合工艺、用途、年代和历史故事深问 |

**“能识别展品”不等于“能深度讲解”。** 83 件基础登记展品可以进入实体解析和来源追踪，但当前没有足够资料生成博物馆讲解员式长回答。

## 3. 重点深问展品

这 3 件是当前最适合演示“原始资料摄取 -> 片段检索 -> 声明引用 -> 受约束回答”的展品。对应证据文本位于 [`content/museum-sources/`](content/museum-sources/)。

| 展品 | ID | 馆别 | 事实数 | 当前可问主题 | 推荐问题 |
| --- | --- | --- | ---: | --- | --- |
| 战国水晶杯 | `warring-states-crystal-cup` | 杭州博物馆（演示数据） | 10 | 年代背景、历史地位、材质、发现经过、尺寸记录、外观、工艺推测、用途解释、研究边界 | “详细讲讲战国水晶杯”“它在哪里发现的”“古人怎么把水晶掏空”“它是喝水还是饮酒用的”“还有哪些问题没有定论” |
| 玉钺组合 | `liangzhu-jade-yue-set` | 良渚博物院 | 8 | 年代、出土、外观组合、尺寸、材质、工艺、用途、研究边界 | “玉钺组合为什么叫组合”“它是怎么发现的”“它如何制作和装配”“它可能有什么用途”“哪些解释仍需保留边界” |
| 玉三叉形器 | `liangzhu-jade-trident` | 良渚博物院 | 8 | 年代、出土、尺寸、材质、工艺、用途、复原讨论、研究边界 | “详细介绍玉三叉形器”“它从哪里出土”“三叉形器怎么制作”“它可能怎样使用”“不同复原方案争论什么” |

重点展品共绑定 10 个公开来源、26 条原子事实。来源文本是项目对公开资料的事实摘编，不是馆方原始 PDF、授权档案或审核稿。

## 4. 常规多维展品

以下 15 件可以围绕已有维度进行常规问答，但不应承诺完整历史叙事或研究争议讲解。表中没有列出的主题应进入资料不足兜底。

| 展品 | ID | 馆别 | 事实数 | 当前可问主题 |
| --- | --- | --- | ---: | --- |
| 织金绫大袖袍 | `yuan-gold-damask-wide-sleeve-robe` | 中国丝绸博物馆 | 6 | 年代、材质、尺寸、外观、用途、工艺 |
| 三国青釉堆塑罐 | `three-kingdoms-celadon-funerary-jar` | 杭州西湖博物馆总馆 | 6 | 年代、尺寸、外观、材质、用途、历史信息 |
| 南宋官窑“大宋国物”垫饼 | `southern-song-dasong-guowu-sagger-pad` | 杭州西湖博物馆总馆 | 6 | 年代、出土、尺寸、外观、材质、工艺 |
| 南宋官窑青瓷八卦熏炉盖 | `southern-song-guan-bagua-incense-lid` | 杭州西湖博物馆总馆 | 6 | 年代、出土、尺寸、外观、材质、用途 |
| 南宋官窑青瓷簋式炉 | `southern-song-guan-gui-incense-burner` | 杭州西湖博物馆总馆 | 6 | 年代、出土、尺寸、外观、材质、用途 |
| 深蓝色菱纹罗袍 | `tang-dark-blue-diamond-gauze-robe` | 中国丝绸博物馆 | 5 | 年代、材质、外观、工艺、历史信息 |
| 清玄色地团花蝴蝶纹袍料 | `qing-butterfly-medallion-robe-fabric` | 中国丝绸博物馆 | 5 | 年代、尺寸、材质、外观、工艺 |
| 环人物纹绫袍 | `northern-dynasties-figured-damask-robe` | 中国丝绸博物馆 | 5 | 年代、材质、尺寸、外观、历史信息 |
| 絁袍残片 | `liao-shi-robe-fragment` | 中国丝绸博物馆 | 5 | 年代、材质、尺寸、工艺、研究边界 |
| 五代至北宋越窑青釉水波纹盏托 | `five-dynasties-northern-song-yue-celadon-cup-stand` | 杭州西湖博物馆总馆 | 5 | 年代、尺寸、外观、材质、用途 |
| 南宋官窑青瓷樽式炉 | `southern-song-guan-zun-incense-burner` | 杭州西湖博物馆总馆 | 5 | 年代、出土、尺寸、外观、材质 |
| 反山M14玉鸟 | `liangzhu-jade-bird-fanshan-m14-259` | 良渚博物院 | 5 | 出土、尺寸、外观、工艺、用途 |
| 吴家埠素面琮 | `liangzhu-plain-cong-wujiabu` | 良渚博物院 | 5 | 出土、尺寸、外观、制作与加工痕迹 |
| 反山M14:223玉璧 | `liangzhu-jade-bi-fanshan-m14-223` | 良渚博物院 | 4 | 出土、尺寸、外观、工艺 |
| 瑶山M7:50玉琮 | `liangzhu-jade-cong-yaoshan-m7-50` | 良渚博物院 | 4 | 出土、尺寸、外观、工艺 |

## 5. 基础登记展品

以下 83 件均来自 `china-national-silk-museum-stage3-catalog.json`，每件只有 1 条官网目录级 `history` 事实。当前只适合确认展品名称和收录来源。

| 展品 | ID |
| --- | --- |
| 晚清民国雪青缎地彩绣喜鹊登梅纹暖耳 | `china-silk-catalog-31590` |
| 晚清民国蓝缎地彩绣松鼠葡萄纹暖耳 | `china-silk-catalog-31589` |
| 晚清民国蓝缎地彩绣鹤鹿同春纹暖耳 | `china-silk-catalog-31588` |
| 晚清民国蓝缎地彩绣童子莲鱼纹暖耳 | `china-silk-catalog-31587` |
| 晚清黑缎地平绣唐诗暖耳 | `china-silk-catalog-31586` |
| 民国苎麻女衫裙 | `china-silk-catalog-30674` |
| 竹制扇套 | `china-silk-catalog-30672` |
| 羊皮女袄 | `china-silk-catalog-30671` |
| 紫缎钉几何纹花边眉勒 | `china-silk-catalog-30568` |
| 白底花卉纹花边 | `china-silk-catalog-30567` |
| 几何蝴蝶纹花边 | `china-silk-catalog-30566` |
| “双兔”牌手帕商标 | `china-silk-catalog-30560` |
| 上海寰球手帕厂广告 | `china-silk-catalog-30559` |
| 民国纸制月份广告画（馆方条目3316） | `china-silk-catalog-3316` |
| 石刀 | `china-silk-catalog-3319` |
| 民国纸制月份广告画（馆方条目3313） | `china-silk-catalog-3313` |
| 绣线 | `china-silk-catalog-3321` |
| 铜烫斗（馆方条目3320） | `china-silk-catalog-3320` |
| 帐钩 | `china-silk-catalog-3317` |
| 铜烫斗（馆方条目3314） | `china-silk-catalog-3314` |
| 梭子 | `china-silk-catalog-3322` |
| 天山牌二十一支棉纱包装纸 | `china-silk-catalog-3312` |
| 民国大和生丝厂包装纸 | `china-silk-catalog-3311` |
| 民国纸制月份广告画（馆方条目3318） | `china-silk-catalog-3318` |
| 民国恒丰纺织股份有限公司股票（第008907号） | `china-silk-catalog-3307` |
| 民国烟囱熨斗 | `china-silk-catalog-3308` |
| “浙杭瑞新织绸公司”织款 | `china-silk-catalog-3310` |
| “浙杭振新织绸公司”织款 | `china-silk-catalog-3304` |
| 陶纺轮 | `china-silk-catalog-3303` |
| 座垫 | `china-silk-catalog-3315` |
| 辑里缫丝车 | `china-silk-catalog-3305` |
| 石元宝 | `china-silk-catalog-3299` |
| 旧碗 | `china-silk-catalog-3309` |
| 榻柜 | `china-silk-catalog-3306` |
| 旧烛台 | `china-silk-catalog-3296` |
| “绸联处”牌 | `china-silk-catalog-3297` |
| 丁桥织机 | `china-silk-catalog-3301` |
| 土丝 | `china-silk-catalog-3295` |
| 丝线（馆方条目3294） | `china-silk-catalog-3294` |
| 碗（馆方条目3293） | `china-silk-catalog-3293` |
| 碗（馆方条目3292） | `china-silk-catalog-3292` |
| 碗（馆方条目3289） | `china-silk-catalog-3289` |
| 碗（馆方条目3288） | `china-silk-catalog-3288` |
| 碗（馆方条目3287） | `china-silk-catalog-3287` |
| 碗（馆方条目3291） | `china-silk-catalog-3291` |
| 碗（馆方条目3290） | `china-silk-catalog-3290` |
| 丝线（馆方条目3298） | `china-silk-catalog-3298` |
| 丝绵胎 | `china-silk-catalog-3300` |
| “开元通宝”铜钱 | `china-silk-catalog-3302` |
| “五铢”铜钱 | `china-silk-catalog-3286` |
| 当代海宁蚕花戏“马鸣王菩萨”牛皮道具 | `china-silk-catalog-3285` |
| 盘金彩绣狮子纹荷包 | `china-silk-catalog-3282` |
| 黄绸地打籽绣多子多福荷包 | `china-silk-catalog-3281` |
| 蓝缎圈金铺绒绣石榴飞雁腰包 | `china-silk-catalog-3280` |
| 白绸绣桃子围棋腰包 | `china-silk-catalog-3279` |
| 红绿缎绣花卉小饰件 | `china-silk-catalog-3284` |
| 品蓝缎彩绣花卉名片袋 | `china-silk-catalog-3283` |
| 白缎铺绒绣桃子螃蟹褡裢 | `china-silk-catalog-3274` |
| 彩绸绣石榴花虫荷包 | `china-silk-catalog-3275` |
| 彩绢圈金绣梅花钱袋 | `china-silk-catalog-3272` |
| 白绸地彩绣公鸡花卉钱袋 | `china-silk-catalog-3273` |
| 蓝缎圈金铺绒绣葫芦桃子褡裢 | `china-silk-catalog-3270` |
| 绿缎彩绣花卉钱袋 | `china-silk-catalog-3269` |
| 粉缎圈金绣螃蟹荷包 | `china-silk-catalog-3271` |
| 红缎地彩绣花蝶荷包 | `china-silk-catalog-3266` |
| 月白绸彩绣花蝶碗形钱袋 | `china-silk-catalog-3278` |
| 红绸彩绣花蝶钱袋 | `china-silk-catalog-3265` |
| 红缎地彩绣多子多福荷包 | `china-silk-catalog-3264` |
| 五彩缎彩绣花蝶钱袋 | `china-silk-catalog-3267` |
| 紫色缎地彩绣花蝶钱袋 | `china-silk-catalog-3276` |
| 白缎彩绣花卉钱袋 | `china-silk-catalog-3268` |
| 蓝缎彩绣花卉荷包 | `china-silk-catalog-3262` |
| 红布彩绣莲藕飞蝶钱袋 | `china-silk-catalog-3259` |
| 白色布地彩绣桃花钱袋 | `china-silk-catalog-3258` |
| 堆绒绣花卉碗形钱袋 | `china-silk-catalog-3257` |
| 红缎彩绣花蝶荷包 | `china-silk-catalog-3277` |
| 白绢彩绣花蝶钱袋 | `china-silk-catalog-3260` |
| 白缎彩绣小花钱袋 | `china-silk-catalog-3253` |
| 湖绿绢彩绣花卉钱袋 | `china-silk-catalog-3255` |
| 宝蓝缎铺绒绣花卉荷包 | `china-silk-catalog-3254` |
| 堆绫绣花卉钱袋 | `china-silk-catalog-3250` |
| 白缎彩绣桃花碗形钱袋 | `china-silk-catalog-3252` |
| 红缎地彩绣葫芦莲花钱袋 | `china-silk-catalog-3249` |

## 6. 下一批资料建设顺序

1. 从 15 件常规多维展品中选择 5 至 10 件，优先补齐发现经过、工艺、用途和研究边界。
2. 每件建立来源 manifest、可定位原文片段、来源版本和 `claim_support`，达到重点深问门槛后再升级分级。
3. 83 件基础登记展品按实际演示价值分批扩充，禁止为凑数量生成同质 PDF 或用模型常识补写。
4. 每次内容扩充后重新运行批量审计、来源 manifest 校验、重点展品回归和引用覆盖检查。

相关文档：

- [`content/museum/README.md`](content/museum/README.md)
- [`content/museum-sources/README.md`](content/museum-sources/README.md)
- [Demo RAG 与硬件平台计划](../../docs/roadmap/demo-rag-hardware-platform-plan.md)
- [RAG 规模化路线](../../docs/roadmap/2026-08-rag-scale-up-plan.md)
