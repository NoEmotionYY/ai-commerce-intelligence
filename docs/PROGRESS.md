# 项目进度

## 当前状态

- 当前阶段：后验收 UI 回归完成
- 项目完成状态：COMPLETE
- 最后更新：2026-08-14

## 已完成里程碑

### 2026-08-14：人工 UI 验收重新打开

- 人工 Streamlit 验证复现 Copilot `KeyError`、Approval Center `TypeError` 与受保护 Crawler 操作错误渲染。
- 确认根因是前端没有 HTTP 状态/JSON/响应 shape 契约，旧 E2E 又绕过了 Streamlit。
- 旧 `FINAL_REPORT` 的完成结论暂停生效，详见 `POST_ACCEPTANCE_REGRESSION.md`。

### 2026-08-14：后验收 UI 回归修复与实际验证

- 建立 `FrontendApiClient`，统一校验 HTTP 401/403/404/422/500、超时、网络、非法 JSON、缺失字段和错误对象/列表类型。
- 五个 Streamlit 页面全部只消费已验证 Pydantic 模型；受控错误显示中文提示，未知错误写日志且不向用户泄漏 traceback。
- 修复 Copilot 缺失 `answer`、Approval 错误字典迭代、Crawler 鉴权错误误当成功，以及 Dashboard/Market 同类契约风险。
- 操作员凭据贯穿 Copilot、Approval 列表/日志和 Crawler 代理；审批凭据独立传给批准/拒绝端点；空、错误和正确凭据均有 AppTest 与浏览器证据。
- 采购增加请求级幂等键和 `0002_approval_idempotency` 迁移；顺序/并发同键请求复用草稿，同一审批重放复用 ERP 采购单，相反决定返回 409。
- 新增 Streamlit AppTest、前端客户端负面测试、Agent 契约测试、Crawler RUNNING 持久化测试和真实 8501 Playwright E2E。

本轮实际验证：

- 普通本地套件：`56 passed, 9 skipped`；9 项均为显式环境门控的 5 项 Compose 与 4 项浏览器 E2E。
- 干净 Compose API E2E：`5 passed in 24.62s`，含真实 MySQL 并发采购幂等。
- 干净 Compose 浏览器 E2E：`4 passed in 25.68s`，覆盖五页、A102、B205、凭据和四类 Crawler。
- `ruff check`、`ruff format --check`、`mypy commerce frontend` 全部通过。
- `docker compose down -v` 后 `docker compose build --pull --no-cache` 成功；全栈 6 个运行服务均 healthy。
- 干净 MySQL 迁移版本 `0002_approval_idempotency`，seed 后 50 商品、10000 订单；E2E 后审批/采购数据真实持久化。
- Frontend 日志未发现 `Traceback`、`KeyError`、`TypeError` 或 `streamlit_unexpected_error`。
- Reviewer 1/2/3/4 最终均报告 Critical 0、High 0；总体审查确认可恢复 `COMPLETE`。

### 2026-08-13：权威文档读取与初始审计

- 完整读取 `AGENTS.md`、`docs/PROJECT_SPEC.md`、`docs/ACCEPTANCE.md`。
- 确认仓库最初仅包含上述三份文档，无现有实现可迁移。
- 创建架构、任务、进度和决策记录。
- 启动独立的规格、仓库和测试基础只读审查。

实际验证：

- Git 根目录：`D:\Github\ai-commerce-intelligence`
- 初始分支：`master`
- 初始工作区：干净
- Docker CLI/Engine：29.5.3
- Docker Compose：v5.1.4
- Python：3.11.7
- Git：2.45.1.windows.1

## Phase 0 规格审计结论

### 矛盾与缺失

- 规格要求分析竞品历史价格，但建议表结构只有当前商品快照；增加独立价格历史表。
- 规格一处把“创建采购单”视为审批后动作，另一处列出“采购草稿”；统一为草稿可在本系统创建，ERP 正式采购单只能在批准后创建。
- “100% 可运行”无法作为工程保证；改为本地确定性数据源、自动健康检查和 E2E 验证。
- 未定义日期窗口、时区、金额精度、零销量和零广告消耗行为；实现中统一 UTC、`Decimal` 和显式零值规则。
- 未提供 LLM 凭据与模型可用性；采用可选真实模型和默认离线路由，共用真实工具与数据。
- 未定义公开网站允许范围；采用配置允许列表，默认只启用本地模拟站。
- 未定义审批并发、重复提交、过期和身份可信边界；使用事务、状态机、幂等键和服务端再校验。

### 简化项

- 不引入 Redis/Celery、Kafka、复杂多 Agent 或 React。
- 共享领域包减少重复代码，同时保留三个可独立部署服务。
- 测试用 SQLite 提速，最终必须用 MySQL Docker 实测。

### 安全关注

- 禁止 Agent/LLM 获取任意 SQL 写能力。
- 采购执行不能仅依赖 UI 或提示词。
- Crawler 需要防 SSRF、禁止凭据 URL 和非 HTTP(S) scheme，并校验重定向目标。
- Tool 日志只记录调用信息，不记录模型私有推理或敏感配置。

## 未完成与阻塞

- 当前没有真实外部阻塞。
- 未配置外部 OpenAI 凭据，因此真实云模型联网调用未执行；离线路由、LangChain Tools 与真实服务链路已验证。

## 2026-08-14：Phase 1 至 Phase 8 实现里程碑

已实现：

- SQLAlchemy 数据模型、Alembic 初始迁移、幂等 seed。
- Mock ERP 商品、订单、库存、广告与受保护采购 API。
- 财务、库存、补货、异常、竞品价格、内容趋势和评论主题确定性分析。
- HTTPX JSON/HTML 与 Playwright 动态采集、任务状态、重试、限速、校验、去重和允许列表。
- LangChain Tools 真实连续调用与操作日志。
- LangGraph `interrupt/resume` 采购工作流及持久审批记录。
- Agent API、日报、Dashboard、五页面 Streamlit UI。
- 六服务 Docker Compose 与健康检查。

实际验证：

- `python -m pytest -q`：21 passed，4 个 Compose 用例按设计在普通测试中跳过。
- `python -m ruff check .`：通过；`python -m ruff format --check .`：通过。
- `python -m mypy commerce`：23 个源文件无问题。
- `docker compose build --no-cache`：通过；随后稳定源码增量重建通过。
- `docker compose down -v` 后全新 MySQL 卷启动，6 个运行服务均 healthy。
- MySQL：16 张业务表，50 商品、10000 订单、30 广告；`workflow_checkpoints` 存在并被实际写入。
- 显式 `RUN_COMPOSE_E2E=1`：4 passed，覆盖 HTTPX、HTML 分页、Playwright、A102 五工具联合分析和 B205 审批执行。
- 独立规格、安全、财务/Crawler/测试和最终验收审查已执行；所有 Critical/High 已修复并复测。
