# v0.1.7 维护验证

修复单日数量要求、后端地址变化后的代理恢复，以及攻略原句引用格式一致性。

- 单日数量/候选/最终质量/旧检查点兼容：228 项隔离测试通过。
- 攻略回答/引用支持/离线供应商契约：23 项隔离测试通过。
- 实际 Nginx 后端 IP .2 → .3，前端不重启，4.4 秒恢复；POST URI/body、共享链接缓存、SSE 与私有路径检查通过，见 [回执](nginx-dns-regression.json)。
- 已审查的原生包与嵌入式 Chroma 适用性边界未改变，见[源码差异复核](source-delta-review.json)。新源码 CI、最终镜像扫描、真实供应商验证及公开发布仍待执行，本文不提前声明通过。

历史 v0.1.6 及其失败记录保留，不覆盖原标签或镜像。

真实配置供应商验收已通过：原始单日输入生成1个景点，保存刷新保持；攻略返回`supported_excerpt_answer`且支持检查通过，见[真实回归回执](functional-regression.json)。新增text2/amap34/embedding6/vision0；历史额度不重置。

OpenTelemetry Python 7个关联包升级至官方补丁，隔离安装检查通过；实际修补镜像56项直接回归通过，读取同一真实记录与5来源检索通过且0新调用，见[镜像复验](security-image-replay.json)。Go Alertmanager/Caddy同时修复SDK低级别告警；原始失败保留，最终安全扫描与CI仍待终态。

最新候选镜像的代理测试另覆盖健康接口Host，后端换IP后8.62秒恢复，前端未重启；见[实际封装配置回执](packaged-nginx-health.json)。这些单次恢复时间不是SLA。
