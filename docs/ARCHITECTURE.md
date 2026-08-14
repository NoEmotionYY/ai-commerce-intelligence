# 系统架构

## 1. 架构目标

本系统以可重复演示、可测试和安全的业务写入边界为首要目标。核心链路为：

1. 企业运营：自然语言请求 → Agent → 已验证工具 → Mock ERP REST API → 数据库 → 确定性分析。
2. 市场情报：Crawler Service → HTTPX/Playwright → 清洗、校验、去重 → 数据库 → 营销分析。
3. 业务执行：Agent 建议 → LangGraph 审批中断 → 人工批准/拒绝 → Mock ERP API → 审计日志。

## 2. 运行时组件

- `agent-api`：统一业务 API、Agent 编排、审批入口、Dashboard 和报告。
- `mock-erp`：商品、订单、库存、广告和采购执行 REST API。
- `crawler-service`：受控目标的 HTTPX/Playwright 采集、任务状态和持久化。
- `mock-competitor-site`：确定性的本地竞品 JSON、HTML 和动态页面。
- `frontend`：Streamlit Dashboard、AI Copilot、市场情报、审批中心和爬虫中心。
- `mysql`：共享持久化数据库。

## 3. 代码分层

```text
API / UI
  ↓
Agent 编排 / LangGraph 工作流
  ↓
已验证 LangChain Tools
  ↓
业务服务 / ERP 与 Crawler 客户端
  ↓
SQLAlchemy Repository / 外部 REST API
  ↓
MySQL
```

领域模型、数据库会话、确定性计算和公共 Schema 放在共享 Python 包中。各服务仅通过明确入口暴露能力，避免业务规则复制。

## 4. 数据与事务边界

- SQLAlchemy 2.x 定义业务表、竞品表、任务、审批、会话与操作日志。
- Alembic 管理数据库迁移；种子脚本可幂等地生成稳定演示数据。
- 测试默认使用 SQLite，Docker 和正式演示使用 MySQL。
- 金额使用 `Decimal`，时间统一为 UTC 感知时间。
- 唯一键保证订单、竞品快照和采购执行的幂等性。

## 5. 安全边界

- LLM 和 Agent 不接收数据库连接或任意 SQL 工具。
- 所有工具输入均由 Pydantic 校验，并记录公开的工具输入/输出摘要；不记录模型私有推理。
- 采购执行必须校验审批任务状态为 `APPROVED`，且使用事务和幂等键防止重复执行。
- Crawler 仅允许配置的本地模拟站及显式允许的公开来源；拒绝私网探测、任意 scheme、凭据 URL 和重定向越界。
- 不实现登录绕过、验证码破解或访问控制规避。

## 6. Agent 策略

- 使用单 Agent + LangChain Tools + LangGraph。
- Provider 抽象至少支持 `offline` 与 `deepseek`。DeepSeek 使用官方兼容接口和真实 LangChain Tool Calling；离线模式采用确定性意图路由器。两者共享同一批 Tool、业务服务与响应契约。
- 云模型只获得当前意图所需的只读工具；采购批准、ERP 执行和 Crawler 写操作不进入云模型工具集。
- A102 等强制场景同时验证工具名称与实体参数，工具结果由服务端留存。变化率、金额、风险与证据由共享 Python 组合器生成，模型只负责工具选择和自然语言解释。
- B205 云路径只读取库存、商品与销量；推荐数量、金额、幂等草稿和 `PENDING` 状态由确定性服务生成，后续仍由 LangGraph 人工审批链路控制。
- 本地路由不是硬编码业务答案：所有数字和结论均来自 ERP、数据库和 Python 分析服务。
- Agent 返回 Pydantic 结构化响应，包含答案、证据、工具调用和待审批动作。

## 7. Crawler 策略

- HTTPX 用于 JSON 与静态 HTML，Playwright 用于 JavaScript 渲染页。
- 同一管理器实现超时、有限重试、指数退避、分页、限速、目标校验、数据校验、去重、持久化和失败记录。
- 任务状态为 `PENDING → RUNNING → SUCCESS/FAILED`。
- Playwright 浏览器不可用时任务明确失败，不伪装为成功。

## 8. 可观测性与失败恢复

- 服务输出结构化日志并提供 `/health`。
- Tool 调用写入 `operation_logs`，包含 request/session/tool、耗时、状态与公开输入输出。
- Crawler 错误写入任务记录。
- LangGraph 进程内使用 `InMemorySaver` 完成 interrupt/resume；审批状态与恢复镜像持久化到数据库，进程重启后由数据库记录重建图状态。重复批准不会重复创建采购单。

## 9. 验证层次

- 单元测试：财务、库存、异常检测、清洗、解析、目标校验。
- 集成测试：数据库、Mock ERP API、Crawler API、Agent Tools。
- 工作流测试：草稿、审批、拒绝、恢复、幂等和禁止提前执行。
- E2E：经营链路、Crawler 链路、A102 联合分析、B205 审批采购。
- 运行时验证：Lint、类型检查、Docker 全量构建、Compose 健康检查、干净数据库迁移和 seed。
