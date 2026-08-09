# 小芯文档中心

这里是小芯项目的文档入口。小芯是在小智 ESP32 语音 AI 服务基础上做的二次开发项目。

当前策略很明确：先保证继承来的系统能稳定运行，再按小芯自己的产品方向逐层改造。

## 产品文档

- [产品领域词汇表](product/domain-language.md)

## 入门文档

- [部署说明](getting-started/deployment.md)
- [模型服务商配置](getting-started/model-providers.md)
- [首次运行检查清单](getting-started/first-run-checklist.md)

## 开发文档

- [系统架构](development/architecture.md)
- [定制开发说明](development/customization.md)
- [运行路径说明](development/runtime-paths.md)
- [OTA 与 WebSocket 路径闭环](development/xiaoxin-ota-websocket-paths.md)
- [陪伴记忆 V2 设计规格](superpowers/specs/2026-07-18-xiaoxin-companion-memory-v2-design.md)
- [陪伴记忆 V2 实施计划](superpowers/plans/2026-07-18-xiaoxin-companion-memory-v2-implementation.md)
- [陪伴记忆 V2 运行路径](development/runtime-paths.md#陪伴记忆-v2-运行路径)
- [小芯需求工作台本地服务启动说明](requirements/start-local-service.md)

## 运维文档

- [工程审计清单（2026-07-21）](operations/2026-07-21-engineering-audit.md)
- [故障排查](operations/troubleshooting.md)
- [备份与升级](operations/backup-and-upgrade.md)
- [真机闭环验收台账](operations/xiaoxin-real-device-acceptance-ledger.md)
- [陪伴记忆 V2 真机验收状态](operations/xiaoxin-real-device-acceptance-ledger.md#陪伴记忆-v2-真机验收2026-07-18未执行)
- [当前交接快照](operations/current-handoff.md)

## 决策记录

- [决策记录目录](decisions/README.md)

## 上游文档归档

原始小智项目文档已经归档到：

```text
docs/upstream-archive/
```

常用归档入口：

- [原始全模块部署文档](upstream-archive/original-docs/Deployment_all.md)
- [原始单服务部署文档](upstream-archive/original-docs/Deployment.md)
- [原始常见问题](upstream-archive/original-docs/FAQ.md)

归档文档只作为参考资料使用。其中部分相对链接仍可能指向归档前的原始路径。
