# 最终验收报告

## 结论

项目已达到仓库 Definition of Done。A102 场景由真实 LangChain 工具返回的 ERP 销量、广告、商品价格以及 Crawler 竞品价格历史、内容趋势共同生成结论；B205 场景在审批前不写 ERP，审批后由受信服务按数据库中批准内容幂等执行。

## 实际验证 PASS

- 本地测试：`21 passed`；普通套件中 4 个 Compose 用例按环境标记跳过，已在真实 Compose 中单独运行并 `4 passed`。
- Ruff 检查与格式检查通过；Mypy 对 23 个源文件通过。
- `docker compose build --no-cache` 通过；从删除旧卷开始完成干净 MySQL 初始化、迁移和确定性 seed。
- MySQL 实测 16 张业务表，50 个商品、10000 个订单、30 条广告记录，工作流恢复表存在并有真实记录。
- MySQL、Agent API、Mock ERP、Crawler、模拟竞品站和 Streamlit 均显示 healthy；应用健康检查包含数据库连通性。
- HTTPX JSON、静态 HTML 分页、Playwright 动态采集、任务持久化和 Crawler 鉴权链路通过 E2E。
- A102 E2E：5 次真实工具调用，答案直接依赖工具输出；测试证明替换工具数据会改变答案。
- B205 E2E：草稿为 PENDING 时 ERP 采购单数量不变；无审批凭据被拒绝；批准后创建且审批状态变为 EXECUTED。
- 安全复核修复了默认共享审批密钥、金额篡改、审批竞态、Playwright 子资源 SSRF、秘密扩散和 Crawler 未鉴权等 Critical/High 项。

## 已知限制

- 默认离线意图路由用于可重复演示；已实现真实 OpenAI `bind_tools`/ToolMessage 循环，但因未提供外部密钥，云模型联网调用未执行，不能将其报告为已验证 PASS。
- LangGraph 的运行 checkpoint 使用进程内 `InMemorySaver`；数据库持久化审批业务状态和恢复镜像，重启时重建图状态，并非数据库原生 LangGraph saver。
- 普通 `pytest` 为快速反馈会跳过 Compose E2E；最终验收已显式启用并实际运行。
- Playwright 动态适配器面向确定性单页模拟站；HTTPX 适配器承担分页。真实公网适配器需针对目标站点条款、robots 和页面结构显式启用。
- 尚无高并发审批/采购压力测试；数据库行锁、唯一约束、服务端批准内容绑定和幂等返回已实现并覆盖主要顺序行为。

## 外部阻塞

- 无阻止本地完整 Demo 的外部阻塞。
- 外部 LLM 提供方验证缺少用户提供的有效凭据，按已知限制处理，不影响默认离线验收链路。
