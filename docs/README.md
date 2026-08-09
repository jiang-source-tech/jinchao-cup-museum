# 金潮杯博物馆项目文档

这里是小芯博物馆智能伴游系统唯一有效的项目文档中心。2026 年 8 月 9 日以前从旧学生陪伴项目继承的 `docs` 内容已经删除，不再作为需求、部署或验收依据。

## 阅读顺序

1. [产品需求文档（PRD）](product/PRD.md)
2. [业务层重建设计](architecture/business-rebuild.md)
3. [当前服务端与固件运行链路审计](architecture/current-runtime-audit.md)
4. [业务层重建实施方案](roadmap/business-rebuild-execution-plan.md)
5. [领域数据模型](domain/data-model.md)
6. [服务端与固件合同](protocol/server-firmware-contract.md)
7. [2026 年 8 月实施路线](roadmap/2026-08-competition-plan.md)

## 架构决策

- [ADR-0001：保留小芯设备传输路径，独立重建博物馆业务运行时](adr/0001-rebuild-museum-runtime-behind-xiaoxin-transport.md)
- [ADR-0002：以展品事实和资料来源作为回答依据单位](adr/0002-ground-answers-in-reviewed-facts.md)

## 文档状态规则

- “现状”只描述仓库已经存在并经过核对的能力。
- PRD 是产品范围、用户体验和验收标准的上位依据；架构与路线图不得扩大 PRD 范围。
- “目标”描述尚未实现的设计，不得写成已经完成。
- “验收”必须记录真实执行步骤和可复核结果。
- 未确认的服务器、展馆、合作方、域名和比赛日期必须标记为待确认。
