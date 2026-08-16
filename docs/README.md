# 金潮杯博物馆项目文档

这里是金潮杯博物馆项目唯一有效的文档中心。2026 年 8 月 9 日以前从旧学生陪伴项目继承的内容已经删除或明确标记为历史资料，不再作为当前需求、部署或验收依据。

## 当前内容快照

- 2026 年 8 月 15 日仓库候选内容：8 个内容包、4 家馆、101 件展品、187 条事实、108 个来源。
- 可提问深度：3 件重点深问、15 件常规多维、83 件基础登记。
- 当前没有真实馆方对接；公开资料、演示整理资料和测试数据不得称为馆方授权或馆方审核内容。
- 最近一份生产报告记录的是 2026 年 8 月 13 日的 100 件展品、171 条事实、104 个来源历史快照，不代表当前仓库新增内容已经部署。

## 项目控制

- [服务端展品与可提问范围清单](../main/xiaozhi-server/EXHIBIT_CATALOG.md)
- [需求追踪与交付看板](requirements/index.html)
- [需求数据与维护说明](requirements/README.md)

## 阅读顺序

1. [产品需求文档（PRD）](product/PRD.md)
2. [展品级可信语音 RAG 系统详细设计](architecture/exhibit-rag-design.md)
3. [Demo RAG 与硬件对话平台计划](roadmap/demo-rag-hardware-platform-plan.md)
4. [RAG 规模化路线](roadmap/2026-08-rag-scale-up-plan.md)
5. [展品级语音 RAG 实施计划](roadmap/exhibit-rag-execution-plan.md)
6. [业务层重建设计](architecture/business-rebuild.md)
7. [当前服务端与固件运行链路审计](architecture/current-runtime-audit.md)
8. [业务层重建实施方案](roadmap/business-rebuild-execution-plan.md)
9. [领域数据模型](domain/data-model.md)
10. [服务端与固件合同](protocol/server-firmware-contract.md)
11. [2026 年 8 月实施路线](roadmap/2026-08-competition-plan.md)
12. [`121.43.33.0` 博物馆服务部署方案](production-deployment-plan.md)

## 架构决策

- [ADR-0001：使用唯一博物馆传输路径，独立运行博物馆业务](adr/0001-isolate-museum-runtime-and-transport.md)
- [ADR-0002：以展品事实和资料来源作为回答依据单位](adr/0002-ground-answers-in-reviewed-facts.md)

## 文档状态规则

- “现状”只描述仓库已经存在并经过核对的能力。
- PRD 是产品范围、用户体验和验收标准的上位依据；架构与路线图不得扩大 PRD 范围。
- “目标”描述尚未实现的设计，不得写成已经完成。
- “验收”必须记录真实执行步骤和可复核结果。
- `stage1`、`stage3`、`stage4` 等报告是对应日期和提交的历史验收快照，不得把其中数字当作当前仓库实时统计，也不得反向改写历史结果。
- 未确认的服务器、展馆、合作方、域名和比赛日期必须标记为待确认。
