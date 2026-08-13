# 最终验收报告

## 结论

2026-08-14 的人工 Streamlit 验证使旧验收结论失效。本报告只采用后验收回归修复完成后的新证据。项目已重新达到 `COMPLETE`：五个页面均由真实浏览器执行，已观察缺陷全部修复，四项独立复审均为 Critical 0、High 0。

## 实际验证 PASS

- 集中式前端 API 客户端统一检查 HTTP 状态、超时、网络、JSON 和 Pydantic 响应 shape；页面不再盲取 `answer` 或把错误对象当业务列表。
- 空/错误 operator、空/错误 approver 均显示受控中文错误且不产生 Streamlit exception；正确凭据可执行受保护操作。
- 普通本地套件：`56 passed, 9 skipped in 15.78s`。9 项均为显式环境门控的 5 项 Compose API E2E 与 4 项浏览器 E2E，不作为通过项计入该数字。
- 干净 Compose API E2E：`5 passed in 24.62s`，覆盖健康/ERP、HTTPX/HTML/Playwright Crawler、A102、B205 以及真实 MySQL 并发同键幂等。
- 干净 Compose Streamlit Playwright E2E：`4 passed in 25.68s`，实际访问 8501 并穿过 Streamlit → Agent → ERP/Crawler → MySQL。
- Dashboard：订单量、收入、利润、利润率、ROAS、市场异常与库存预警可见，无 traceback。
- AI Copilot：浏览器输入“为什么我们的 A102 最近销量下降？”，答案、证据和工具轨迹可见；本次调用实际增加 ERP 销量、广告、内部价格及 Crawler 竞品价格历史、内容趋势五类工具日志。
- Approval Center：B205 从真实 UI 创建 PENDING 草稿；空/错误审批凭据不创建采购单；正确审批后状态为 EXECUTED 且 ERP 只有一张对应采购单；重放返回已有采购单。
- 采购并发幂等：两个独立 HTTP/数据库会话用同一操作键并发请求，均返回 200 和同一 approval ID；唯一约束及冲突恢复保证单草稿。
- Crawler Center：空/错误 operator 受控失败；商品、内容、评论、动态 Playwright 四按钮均创建真实任务并达到 SUCCESS，records > 0，包含开始/结束时间；重复商品采集不增加业务唯一集合。
- Crawler 状态：可控单元测试在任务工作尚未完成时读取到数据库 RUNNING；浏览器验证最终 SUCCESS。
- Market Intelligence：竞品商品、评论分析、热门内容与市场趋势均在真实页面渲染，API 错误由统一边界处理。
- `python -m ruff check .`、`python -m ruff format --check .`、`python -m mypy commerce frontend` 全部通过。
- `docker compose down -v` 后执行 `docker compose build --pull --no-cache` 成功，约 200.3 秒；随后完整栈启动成功。
- MySQL、Agent API、Mock ERP、Crawler Service、Mock competitor site、Streamlit 六个运行服务全部 healthy。
- 干净数据库升级到 `0002_approval_idempotency`；seed 生成 50 商品、10000 订单；审批与采购在 E2E 中真实持久化。
- Frontend 容器日志未发现 `Traceback`、`KeyError`、`TypeError` 或 `streamlit_unexpected_error`。
- 四项独立复审覆盖前端/API 契约、认证审批安全、UI E2E 和总体规格验收，最终均为 Critical 0、High 0。

## 已知限制

- 未提供外部 OpenAI 凭据，因此真实云模型联网 Tool Calling 未执行；默认离线路由仍调用真实 LangChain Tools、服务 API 和确定性业务逻辑，不硬编码业务答案。
- LangGraph 使用进程内 `InMemorySaver`；数据库持久化审批业务状态和恢复镜像，重启后重建图状态，不是数据库原生 LangGraph checkpointer。
- 多数非聊天 Agent 端点尚未声明 FastAPI `response_model`，前端模型也未全部启用 strict；集中客户端已对本轮所有消费路径验证状态与 shape，但服务端契约仍可进一步收紧。
- 浏览器测试通过 Compose 中的官方 Playwright 镜像运行；宿主机没有单独安装 Chromium。README 已记录可重复命令。
- 浏览器对 Crawler 验证最终 SUCCESS；短暂 RUNNING 的真实数据库状态由独立可控测试验证。
- 审批 Demo 使用共享角色凭据，审计主体为 `demo-approver`；生产部署应接入真实身份系统，并将幂等键绑定请求指纹。
- `0001_initial` 的历史实现依赖当前 SQLAlchemy metadata；`0002` 已防御干净/存量两种结构并经过干净 MySQL 验证，后续迁移宜改为固定 DDL 快照。

## 外部阻塞

- 无阻止本地完整 Demo 和本轮验收的外部阻塞。
- 真实云 LLM 验证仅因未提供供应商凭据而未执行，已列为已知限制，未报告为 PASS。
