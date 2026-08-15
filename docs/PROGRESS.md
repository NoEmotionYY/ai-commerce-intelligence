# 项目进度

## 当前状态

- 当前阶段：Apache-2.0 开源许可证发布完成
- 项目完成状态：COMPLETE
- 最后更新：2026-08-16

## 已完成里程碑

### 2026-08-16：Apache-2.0 开源许可证发布

- 新增 Apache License 2.0 官方完整许可证文本，并在 Python 包元数据中声明 `LICENSE` 文件。
- README 增加许可证说明和入口，GitHub 可识别仓库许可证。
- 当前工作树验证：`93 passed, 18 skipped`；Ruff、格式检查、Mypy（28 个源文件）、许可证元数据检查和 `git diff --check` 全部通过。

### 2026-08-14：DeepSeek 真实云 Tool Calling 完成

- 新增 `offline` / `deepseek` Provider 抽象；DeepSeek 只从环境读取密钥，并限定官方 HTTPS 端点、已验证模型、超时与工具调用预算。离线确定性路径保持不变。
- DeepSeek 使用真实 LangChain `bind_tools → tool_calls → ToolMessage`。A102 强制核验五类工具及实体参数，最终指标和证据由真实工具结果交给共享 Python 组合器生成，不信任模型自报数据。
- B205 的云模型只能调用只读库存、商品与销量工具；采购数量、金额和草稿由确定性业务服务生成，状态停在 `PENDING`。只有独立审批员凭据可批准，ERP 执行保持唯一与幂等。
- 本地完整套件：`93 passed, 18 skipped in 26.47s`。跳过项均是需要显式 Compose、云凭据、浏览器或故障注入开关的环境门控用例，已在对应真实环境单独执行。
- 真实 DeepSeek 云套件：`7 passed in 59.37s`；使用模型 `deepseek-v4-pro`，没有 mock 云响应。
- DeepSeek Compose 浏览器套件：`5 passed, 1 skipped in 48.50s`；跳过项仅为单独执行的故障注入场景。浏览器断言当前提供商与模型，不能在离线模式假通过。
- 离线 Compose API：`5 passed in 21.61s`；离线浏览器：`5 passed, 1 skipped in 22.72s`。验证新增云路径没有削弱原确定性路径。
- 无效密钥、极短超时和真实网络不可达分别得到受控中文 `502`、`504`、`502`；浏览器故障场景分别为 `1 passed in 4.37s` 与 `1 passed in 4.02s`，没有新增审批、前端 traceback 或凭据泄漏。
- 最终无缓存构建和全栈重建成功，耗时 `265.2s`；六服务 healthy。干净数据卷迁移到 `0002_approval_idempotency`，16 张表、50 商品、10000 订单。
- 安全边界收尾后再次从当前工作树重建全栈，耗时 `239.5s`；容器源码哈希与工作树一致。最终 Compose API `5 passed in 45.23s`，DeepSeek 浏览器 `5 passed, 1 skipped in 46.97s`。
- Ruff、格式检查、Mypy（28 个源文件）和 `git diff --check` 全部通过。
- 三方向独立复审发现的密钥 repr、工具参数校验、模型证据可信边界、确定性计算、云浏览器假通过与异常边界问题均已修复；最终 Critical 0、High 0。

### 2026-08-14：DeepSeek 云模型验收重新打开

- 用户提供本地 `.env` DeepSeek 配置；审计仅确认配置项存在且非空，没有输出或记录密钥值。
- 现有离线确定性路径保持不变；现有云代码只支持 OpenAI 特判，尚未执行 DeepSeek。
- 官方文档当前标准端点为 `https://api.deepseek.com`，当前 Tool Calling 模型为 `deepseek-v4-pro` / `deepseek-v4-flash`；旧 `deepseek-chat` 已退役。
- 现有云返回位于 B205 确定性草稿分支之前，必须增加安全的云意图后处理，确保模型只选择/调用只读工具，正式采购单仍只能经人工审批创建。
- 本轮旧 FINAL_REPORT 暂停生效；DeepSeek 云连接、真实工具调用、A102、B205 和故障处理必须分别给出新证据。

### 2026-08-14：第二次人工 UI 验证重新打开

- 人工验证发现审批角色仍不清晰，Approval Center 可收到“审批凭据无效”；导航、表格字段、状态、风险、操作类型和原始 JSON 仍暴露英文后端表示。
- 运行态追踪确认两种凭据有意分离：聊天、审批列表、执行记录、采集代理使用 `X-Operator-Key`；批准/拒绝使用 `X-Approver-Key`。不能用有效 operator 代替 approver。
- Streamlit 旧实现把 approver 控件放在审批页条件分支，切出该页后控件状态会被清理；操作员控件始终在侧栏，因此表现为只有审批员凭据丢失。加上无角色说明和验证状态，用户容易把 operator 值当 approver 值。
- 五页大量把 Pydantic 模型直接 `model_dump()` 到 dataframe / JSON，导致 `id`、`action_type`、`CREATE_PURCHASE_ORDER`、`HIGH`、`SUCCESS`、Crawler task type、tool name 等内部稳定值直接进入用户界面。
- 原 `FINAL_REPORT` 再次暂停生效；本轮所有 PASS 必须重新执行并记录。

### 2026-08-14：第二次 UI / 认证加固实现与验证

- 操作员和审批员凭据控件固定在侧栏，使用独立会话状态与密码模式；角色说明和四种验证状态均为中文。
- 新增无副作用认证验证端点；操作员、审批员请求头继续严格分离，不能互相授权。
- 本地生成脚本通过被 Git 忽略的 `.env` 向掩码控件加载随机演示凭据，源码没有固定秘密。
- 新增集中式中文展示层，统一导航、字段、状态、风险、操作、采集类型、工具、来源和未知值降级；五页移除正常视图中的原始 JSON。
- Streamlit 工具栏最小化并关闭详细错误；浏览器测试检查无 `Deploy`、无异常组件、无指定英文内部术语，页面可见文本不含凭据。

本轮重新执行的当前证据：

- 本地完整套件（最终嵌套契约修复后重跑）：`71 passed, 10 skipped in 18.18s`；10 项仅为显式环境门控的 5 项 Compose API 与 5 项浏览器验收，不计为普通套件通过项。
- 前端展示、AppTest、认证契约定向套件：`44 passed in 10.74s`。
- 干净 Compose API E2E：`5 passed in 24.32s`。
- 干净 Compose Streamlit Playwright E2E：嵌套响应契约最终修复后重跑 `5 passed in 25.88s`，覆盖五页中文与凭据保持、认证矩阵、A102/经营日报、B205 和四类采集。
- Ruff、格式检查、Mypy（27 个源文件）、`git diff --check` 均通过。
- 删除数据卷后迁移到 `0002_approval_idempotency`；MySQL 有 16 张业务表，种子为 50 商品、10000 订单。
- 六个运行服务全部 healthy；六服务最近日志未检出 Traceback、KeyError、TypeError、Exception 或 ERROR。
- 并发六目标无缓存导出曾使 Docker Desktop RPC 断开；引擎恢复后对六服务共用的唯一 Dockerfile 串行执行完整 `--no-cache` 构建，生成同一镜像内容并在干净卷成功启动。首次失败未记作 PASS，最终串行构建和启动记为 PASS。
- 第四位总体审查者发现市场日报嵌套 `new_features=null` 会触发 TypeError；改为严格嵌套模型并补客户端/AppTest 后复现为受控中文错误。最终复核 Critical 0、High 0。

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

## 外部阻塞与未执行项

- 当前没有真实外部阻塞。
- DeepSeek 真实云模型联网 Tool Calling 已执行并通过；OpenAI 未配置、未执行，也不报告为 PASS。

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
