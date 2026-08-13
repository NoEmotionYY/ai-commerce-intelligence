# 后验收 UI 回归审计

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
