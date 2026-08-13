# 实施任务

状态说明：`[ ]` 未开始，`[~]` 进行中，`[x]` 已完成且已验证。

## 第二次后验收 UI / 认证加固（重新打开）

- [x] 读取人工反馈并审计 operator / approver 完整凭据链路
- [x] 审计五页导航、表格、枚举、JSON 与 Streamlit chrome 的英文泄漏
- [x] 建立集中式中文本地化与展示层
- [x] 重设计双角色凭据输入、验证状态与演示环境加载
- [x] 移除正常视图中的原始后端 JSON / 英文枚举
- [x] 补齐认证组合矩阵、全中文审计与五页 AppTest
- [x] 从真实浏览器重跑 A102、B205、四类 Crawler 与五页语言审计
- [x] 干净卷、无缓存 Docker 和完整回归
- [x] 四方向独立复审并修复全部 Critical / High
- [x] 重写最终证据、创建 Git 检查点并恢复 COMPLETE

## 后验收 UI 回归（重新打开）

- [x] 记录人工缺陷与全前端根因审计
- [x] 集中式前端 API 契约与错误边界
- [x] 空/错误/正确 operator 与 approver 凭据链路验证
- [x] 修复 Copilot、Approval Center、Crawler Center 及同类页面风险
- [x] 客户端负面测试与 Streamlit AppTest
- [x] 真实 Streamlit Playwright Scenario A-F
- [x] 全量本地/Compose/Docker 回归
- [x] 四方向独立审查、修复 Critical/High
- [x] 重写最终验收证据并恢复 COMPLETE

## Phase 0：规格与架构审计

- [x] 完整读取 AGENTS、PROJECT_SPEC 和 ACCEPTANCE
- [x] 识别规格矛盾、复杂度、安全与可测试性风险
- [x] 建立架构、决策、任务和进度文档
- [x] 独立规格/仓库/测试基础审查并吸收结论
- 验证：文档存在、实现顺序有依赖关系、每阶段有验证条件。

## Phase 1：工程基础、配置和数据库

- [x] Python 包、依赖、环境配置、日志
- [x] SQLAlchemy 模型、Alembic 迁移、数据库会话
- [x] 幂等 seed 与关键异常数据
- [x] 数据库单元/集成测试
- 验证：干净库迁移成功；seed 可重复；必需表与数据量符合配置。

## Phase 2：Mock ERP

- [x] 商品、订单、库存、广告查询 API
- [x] 采购草稿、批准后执行、查询 API
- [x] 参数校验、错误响应、健康检查
- [x] ERP API 测试
- 验证：所有 ACCEPTANCE Mock ERP 条目通过；未批准采购不能执行。

## Phase 3：确定性业务分析

- [x] 收入、成本、利润、利润率、ROI、ROAS、退款率、CAC
- [x] 库存天数、风险分类、补货建议
- [x] 经营异常检测与日报
- [x] 单元和集成测试
- 验证：边界值、零除、金额精度和关键 SKU 异常均有测试。

## Phase 4：Crawler 与模拟竞品站

- [x] JSON、HTML 和动态页面模拟数据源
- [x] HTTPX/Playwright 采集器
- [x] 分页、重试、超时、限速、校验、清洗、去重、持久化
- [x] 任务状态、错误日志、受控目标
- [x] Crawler API 与测试
- 验证：HTTPX 和 Playwright 真正运行；失败路径记录 `FAILED`；重复抓取不重复入库。

## Phase 5：营销情报

- [x] 竞品商品与价格趋势
- [x] 内容热度趋势
- [x] 负面评论和主题统计
- [x] 市场变化与产品机会
- 验证：所有指标来自数据库并由 Python 计算，关键竞品模式可由 seed 数据复现。

## Phase 6：LangChain Tools 与 Agent

- [x] ERP、财务、库存、Crawler、竞品工具
- [x] 工具参数 Schema、操作日志、连续调用
- [x] 自然语言路由与结构化输出
- [x] A102 内外部联合分析
- 验证：工具真实访问服务/数据库；A102 证据含五类强制数据。

## Phase 7：LangGraph 与人工审批

- [x] 状态、路由、中断、恢复、批准、拒绝
- [x] 审批持久化、过期、失败恢复与幂等
- [x] B205 完整采购链路
- 验证：批准前无法执行；拒绝永不执行；批准恢复后仅生成一张 ERP 采购单。

## Phase 8：Streamlit UI

- [x] Dashboard、AI Copilot、市场情报
- [x] 审批中心、Crawler 中心、受控来源 Run Now
- 验证：五个页面/入口可访问，关键操作调用真实 API。

## Phase 9：工程化与 E2E

- [x] Dockerfile、Compose、健康检查、环境模板
- [x] Lint、类型检查、完整测试和 E2E
- [x] README、API、演示和运行说明
- 验证：全新构建、Compose 启动、干净 DB、seed、ERP/Crawler/Agent/审批/A102 场景全部实测。

## Phase 10：独立审查与最终验收

- [x] 独立正确性、安全、Crawler、财务、测试、架构和 Docker 审查
- [x] 修复全部 Critical/High 并复测
- [x] 逐条验收审计
- [x] 仅在证据充分时创建 FINAL_REPORT
- 验证：每项标为 PASS、已知限制或真实外部阻塞，不虚报未执行检查。
