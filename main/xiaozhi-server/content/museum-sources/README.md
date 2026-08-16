# 重点展品公开资料证据包

本目录保存由金潮杯博物馆项目根据公开来源整理的可摄取证据文本。它们不是馆方授权档案、馆方原始 PDF、馆方审核稿或考古发掘报告原件；本项目不保存来源页面图片，也不整页转载网页正文。

截至 2026 年 8 月 15 日，本目录覆盖战国水晶杯、玉钺组合和玉三叉形器 3 件重点展品，共 10 个公开来源，对应内容包中的 26 条原子事实。逐件可提问范围见 [`../../EXHIBIT_CATALOG.md`](../../EXHIBIT_CATALOG.md)。

每个来源文件遵守四条规则：

1. 一篇文件只对应一个公开来源，原始定位由同目录 `manifest.yaml` 保存。
2. 文本采用项目自己的事实摘编，并明确区分确定事实、研究推定和未知边界。
3. Markdown 二级标题是稳定的证据章节名，`claim_support` 用章节名把已发布事实绑定到原文片段。
4. 来源内容发生变化时更新本地文件；摄取器会计算校验和并保留来源版本，不覆盖历史证据。

推荐顺序是在隔离数据库中导入并发布对应的内容修订，再摄取原始资料：

```powershell
python scripts/import_museum_content.py validate --input content/museum/hangzhou-museum-crystal-cup.yaml
python scripts/import_museum_content.py validate --input content/museum/liangzhu-museum.yaml

python scripts/validate_source_manifest.py --manifest content/museum-sources/hangzhou-museum/manifest.yaml
python scripts/validate_source_manifest.py --manifest content/museum-sources/liangzhu-museum/manifest.yaml
```

生产发布仍须经过 `draft -> reviewed -> published` 内容门禁；本目录进入 Git 不等于馆方审核通过。
