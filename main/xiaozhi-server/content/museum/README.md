# 杭州馆方藏品内容集

本目录保存可直接通过内容导入 CLI 校验和导入的馆方资料摘要。内容于 2026-08-11 根据杭州地区博物馆官方网页整理，所有 revision 均保持 `draft`，必须经过项目既有 `review` 和 `publish` 发布门后才能进入游客回答。

## 内容清单

| 内容包 | 馆方 | 藏品 |
| --- | --- | --- |
| `liangzhu-museum.yaml` | 良渚博物院 | 玉钺组合、玉三叉形器 |
| `liangzhu-museum-batch-2.yaml` | 良渚博物院 | 反山M14玉鸟、瑶山M7:50玉琮、反山M14:223玉璧、吴家埠素面琮 |
| `hangzhou-west-lake-museum.yaml` | 杭州西湖博物馆总馆 | 南宋官窑青瓷樽式炉、南宋官窑青瓷八卦熏炉盖 |
| `hangzhou-west-lake-museum-batch-2.yaml` | 杭州西湖博物馆总馆 | 南宋官窑青瓷簋式炉、“大宋国物”垫饼、越窑青釉水波纹盏托、三国青釉堆塑罐 |
| `china-national-silk-museum.yaml` | 中国丝绸博物馆 | 清玄色地团花蝴蝶纹袍料 |
| `china-national-silk-museum-batch-2.yaml` | 中国丝绸博物馆 | 北朝环人物纹绫袍、唐深蓝色菱纹罗袍、元代织金绫大袖袍、辽代絁袍残片 |

## 识别别名

除馆方规范名称和常用简称外，内容包还保存了经过评测的少量 ASR 同音误识别别名，例如“玉越组合”“尊式炉”“八挂熏炉盖”和“蝴蝶纹袍聊”。这些别名只用于绑定规范展品 ID，不会进入事实陈述或改变来源边界；新增误识别写法仍需经过内容审核和冲突校验。

## 来源登记

- 良渚博物院“玉钺组合”：`https://www.lzmuseum.cn/YuQi/201910913103.html`
- 良渚博物院“玉钺”：`https://www.lzmuseum.cn/YuQi/2019611219610.html`
- 良渚博物院“玉三叉形器”：`https://www.lzmuseum.cn/YuQi/2019393530.html`
- 良渚博物院“三叉形器”：`https://www.lzmuseum.cn/YuQi/20198121782.html`
- 杭州西湖博物馆总馆“南宋官窑青瓷樽式炉”：`https://www.westlakemuseum.com/index.php/gcjp/jpzs2/850-gcjp-004.html`
- 杭州西湖博物馆总馆“南宋官窑青瓷八卦熏炉盖”：`https://www.westlakemuseum.com/index.php/gcjp/jpzs2/853-gcjp-007.html`
- 中国丝绸博物馆“清玄色地团花蝴蝶纹袍料”：`https://www.chinasilkmuseum.com/zggd/info_21.aspx?itemid=2639`

第二批内容的逐件官方来源直接登记在对应 YAML 的 `sources` 段。良渚第二批采用杭州市余杭区良渚遗址官方典藏页面；丝绸与西湖藏品继续采用各馆官方网站。

## 整理边界

- 事实陈述使用项目自己的简短摘要，不复制馆方长段原文。
- 尺寸、出土地、年代、材质、纹饰和馆藏流转只在馆方页面明确给出时写入。
- 馆方使用“推测”“尚无定论”的内容继续保留不确定性，不改写成确定结论。
- `image_uri` 暂不填写，也不下载或提交馆方图片。
- 一条事实至少绑定一个官方来源，禁止无来源补充模型常识。
- ASR 误识别只能作为显式审核别名加入，不做无边界模糊匹配或模型猜测。

## 导入方式

在 `main/xiaozhi-server` 下逐个校验和导入：

```powershell
python scripts/import_museum_content.py validate `
  --input content/museum/liangzhu-museum.yaml

python scripts/import_museum_content.py import `
  --input content/museum/liangzhu-museum.yaml `
  --database data/museum.db
```

其他内容包使用相同命令。导入后按 `museum-content-contract.md` 执行 `review` 和 `publish`，不要直接修改数据库状态。
