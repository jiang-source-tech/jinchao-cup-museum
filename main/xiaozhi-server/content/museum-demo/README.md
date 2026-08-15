# 金潮杯博物馆演示资料集

本目录只包含公开资料整理和自动化演示夹具，分别标记为 `demo_curated` 和 `synthetic_demo`，不代表任何真实博物馆的正式审核、授权或生产数据。默认观众检索不会使用 `synthetic_demo` 来源。

摄取前先准备包含 `warring-states-crystal-cup` 展品记录的 SQLite 数据库，然后执行：

```powershell
python scripts/validate_source_manifest.py --manifest content/museum-demo/manifest.yaml
python scripts/ingest_museum_sources.py --manifest content/museum-demo/manifest.yaml --database data/museum-demo.db --run-id pilot-001
```

资料保留 PDF、Markdown、JSON 和 HTML 四种输入格式，便于验证同一展品的异构资料摄取、片段定位和重复摄取幂等性。PDF 文件是项目生成的输入夹具，不是馆方原始档案。
