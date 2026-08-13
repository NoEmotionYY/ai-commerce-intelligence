# 最终验收报告

## 结论

2026-08-14 的第二次人工 Streamlit 验证再次使旧 COMPLETE 和旧报告失效。本报告仅采用本次“最终 UI / 认证加固”完成后的重新执行证据。四类独立审查均已确认 Critical 0、High 0；项目已恢复 `COMPLETE`。

## 本轮修复

- 操作员与审批员是两种独立角色：分析、采购草稿、审批列表、执行记录和采集使用 `X-Operator-Key`；批准/拒绝只使用 `X-Approver-Key`，两种凭据不可互换。
- 两种密码控件始终位于侧栏并保持独立会话状态，页面明确显示未配置、验证中、验证成功或验证失败；本地演示可从被 Git 忽略的 `.env` 加载随机凭据。
- 新增集中式中文展示层，统一导航、字段、状态、风险、操作、采集类型、工具、来源和动态业务术语；未知枚举不会原样回显。
- 五页移除原始 JSON 主视图；采集失败和后端错误仅显示受控中文信息。
- Streamlit 工具栏最小化，隐藏部署/开发控件并关闭详细错误。
- 浏览器回归新增五页中文审计、双角色认证矩阵、A102 与经营日报、B205 审批幂等和四种采集路径。

## 实际验证 PASS

- 本地完整套件：最终重跑 `71 passed, 10 skipped in 18.18s`。10 项仅为显式环境门控的 5 项 Compose API 和 5 项浏览器验收，不计为普通套件通过项；它们已在对应真实环境单独执行。
- 前端展示、AppTest、认证契约定向套件：`44 passed in 10.74s`。
- 干净 Compose API E2E：最终复跑 `5 passed in 21.50s`。
- 干净 Compose Streamlit Playwright E2E：最终重跑 `5 passed in 26.41s`，真实访问 8501 并穿过 Streamlit → Agent API → ERP/Crawler → MySQL。
- 浏览器认证矩阵覆盖 operator/approver 的空、错误、正确值，以及角色密钥互换和 `invalid operator + valid approver`；错误均受控且不产生采购单。
- A102 浏览器路径展示真实答案、证据和活动记录，本次调用增加至少五条必需内部/外部工具记录；经营日报显示“广告投入产出比”，不泄漏 ROAS。
- B205 从浏览器创建待审批任务，空/错误审批凭据不执行；正确审批后为已执行且仅一张采购单；重复相同操作复用审批编号和采购单。
- 商品、内容、评论和动态页面四种采集均从浏览器触发，任务成功、记录数大于零；服务端 RUNNING 和去重由专门测试补充验证。
- 五页中文审计禁止 Dashboard、Copilot、Market Intelligence、Approval Center、Crawler Center、ROAS、SKU、ERP、Crawler、Agent、英文状态枚举和内部字段；浏览器无 Streamlit exception、console.error、requestfailed 或 Deploy。
- `python -m ruff check .`、`python -m ruff format --check .`、`python -m mypy commerce frontend`、`git diff --check` 全部通过。
- 删除 MySQL 数据卷后，迁移版本为 `0002_approval_idempotency`；16 张业务表，种子为 50 商品和 10000 订单。
- 六个运行服务均 healthy；最近运行日志未发现 Traceback、KeyError、TypeError、意外前端异常或 ERROR。
- Docker 并发六目标无缓存导出曾导致 Docker Desktop RPC EOF；该次明确记录为失败。引擎恢复后，对六服务共用的唯一 Dockerfile 串行执行完整 `--no-cache` 构建并成功启动干净栈，最终串行验证为 PASS。

## 独立复审

- 认证/授权：Critical 0、High 0、Medium 0。
- 中文本地化：初审发现 4 项 High（ROAS、SKU/vs、动态文本、采集错误穿透），均修复并复核关闭；最终 Critical 0、High 0。
- 浏览器/UI 回归：Critical 0、High 0；新增 console/request failure 门禁后复核无新高风险问题。
- 总体验收：初审发现市场日报嵌套 `new_features=null` 会触发 TypeError；严格嵌套模型和负测修复后原载荷为受控中文错误且 `app.exception=0`。最终 Critical 0、High 0。

## 已知限制

- 未提供外部 OpenAI 凭据，因此真实云模型联网 Tool Calling 未执行；默认离线路由仍调用真实 LangChain Tools、服务 API 和确定性业务逻辑，未把该外部检查报告为 PASS。
- LangGraph 使用进程内 `InMemorySaver`；数据库持久化审批业务状态和恢复镜像，重启后重建图状态，不是数据库原生 LangGraph checkpointer。
- 多数非聊天 Agent 端点尚未声明 FastAPI `response_model`，前端已对所有当前消费路径执行状态与 shape 校验。
- 浏览器测试通过项目的官方 Playwright 基础镜像运行；宿主机未安装单独 Chromium。
- Streamlit 自带密码/表格图标的辅助名称由框架提供，仍可能是英文；业务导航、字段、状态、错误和数据展示已中文化。
- 部分空集合仍以空表呈现，未全部增加专用中文空态。
- 审批 Demo 使用共享角色凭据；生产部署仍需接入真实身份、失败审计和限流。

## 外部阻塞

- 无阻止本地完整 Demo 的外部阻塞。
- 真实云 LLM 仅因未提供供应商凭据而未执行，属于明确已知限制。
