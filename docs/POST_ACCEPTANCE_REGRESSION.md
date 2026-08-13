# 后验收 UI 回归审计

> 2026-08-14 第二次人工验证再次重新打开项目。第一次回归修复解决了异常处理，但未完成凭据角色 UX 与全中文展示。本文件后续章节记录第二次回归，旧 PASS 不作为新验收证据。

## 第二次回归：认证 UX 与中文展示根因

1. operator 与 approver 是刻意分离的角色和密钥，不应合并或互相授权。operator 通过 `X-Operator-Key` 发起分析、创建草稿、读取审批与运行采集；approver 通过 `X-Approver-Key` 批准或拒绝。
2. 旧 Streamlit 的两个值虽使用不同键，但 approver 控件只在 Approval Center 分支内渲染；切出该页后 Streamlit 会清理控件状态。operator 位于侧栏而始终保留，加上没有角色说明和验证状态，造成用户误以为已填 operator 即具备审批权限。
3. Approval API 客户端正确发送 `X-Approver-Key`，没有错误地改发 operator；“审批凭据无效”表示 approver 输入为空、错误或与当前 Agent API 环境不一致。需要无副作用的角色验证端点和明确状态，而不是弱化鉴权。
4. 页面直接展示模型 dump 和 `st.json`，使内部 API 字段、枚举、工具名与 Crawler 类型泄漏；导航本身仍是英文。这是展示层缺失，不应改动内部字段或数据库值。
5. 修复采用集中式 presentation/localization 层：统一导航、字段、状态、风险、操作、采集类型、工具名与未知值降级；正常业务视图只显示中文卡片和表格，原始 JSON 不作为主展示。

## 第二次回归修复

- 两种凭据控件始终位于侧栏，以密码模式显示并保持独立会话状态；页面解释两种角色不可互换。
- 新增无副作用的操作员/审批员验证端点，界面显示“未配置、验证中、验证成功、验证失败”。
- 本地生成脚本把随机演示凭据分别注入掩码控件，秘密仍仅存在于被 Git 忽略的 `.env`，Python 源码不含固定密钥。
- 五页改用集中式中文展示层；字段白名单、状态/风险/操作/采集/工具映射和未知值安全降级均有单元与 AppTest。
- 正常业务页面不再以原始 JSON 为主要展示，后端技术错误不会原样透传给用户。
- Streamlit 工具栏设为最小模式，关闭详细错误和使用统计；真实浏览器验证页面没有 `Deploy` 和 Streamlit exception。

## 状态

2026-08-14 人工 Streamlit 验证发现真实用户路径缺陷，原 COMPLETE 状态作废并重新打开。

## 根因

1. `frontend/streamlit_app.py` 的所有请求都直接调用 `.json()`，没有先检查 HTTP 状态。
2. FastAPI 的错误契约为 `{"detail": "..."}`；前端却把错误对象当成正常业务对象。
3. Copilot 盲取 `result["answer"]`，403/500 响应因此触发 `KeyError`。
4. Approval Center 把错误字典当审批列表迭代；字典迭代产生字符串键，随后触发 `TypeError`。
5. Crawler 写请求的鉴权拒绝是后端预期行为，但前端将错误 JSON 当成功结果展示。
6. Dashboard 的 `.get(..., 0)` 会掩盖错误对象；Market Intelligence、日报、审批决策、操作日志和任务列表具有同类契约风险。
7. 现有 Compose E2E 直接请求 Agent/ERP/Crawler API，完全绕过 Streamlit 浏览器层，因此无法发现组件崩溃、凭据输入与错误渲染缺陷。

## 凭据链路

- Copilot、审批列表、操作日志和 Crawler Run Now 使用 `X-Operator-Key`。
- 批准/拒绝使用独立的 `X-Approver-Key`。
- Agent API 再用内部 `X-Crawler-Token` 调用 Crawler Service；该令牌不应暴露给浏览器或 Streamlit UI。
- 空/错误凭据应得到受控 403；正确凭据应完成业务动作。任何错误响应都不得进入业务对象渲染逻辑。

## 修复原则与验证门

- 建立集中式 API 客户端：状态码、超时、网络、JSON、对象/列表 shape 与必需字段统一校验。
- Streamlit 只通过统一错误边界渲染业务数据；所有失败显示中文用户错误，不泄露 Python traceback。
- 增加客户端负面单测、Streamlit AppTest 和真实 Compose Streamlit Playwright E2E。
- 重新运行本地、Compose、浏览器、Docker 无缓存构建和独立审查；旧 FINAL_REPORT 不作为本轮证据。
