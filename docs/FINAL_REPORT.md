# V1 / Demo Historical Verification Evidence

> This report records historical V1/Demo verification only. It is not evidence that AI
> Commerce Operations Copilot V2 is COMPLETE. V2 requires the mandatory P0/P1 acceptance,
> migration, tenant isolation, permission, credential, API, workflow, Compose, browser, and
> real-data gates defined in `docs/ACCEPTANCE.md`.

# 历史验收报告

## 结论

项目于 2026-08-14 完成 V1/Demo 的 DeepSeek 真实云模型 Tool Calling 验收；该历史结论不适用于 V2，V2 当前状态为 `NOT_COMPLETE`。

本报告只记录实际执行的检查。DeepSeek 密钥仅从本地环境加载，未写入源码、日志、界面、Git 或本报告。

## 强制结果

| 验收项 | 结果 | 实际证据 |
|---|---|---|
| 离线确定性路径 | PASS | Compose API `5 passed in 21.61s`；真实 Streamlit 浏览器 `5 passed, 1 skipped in 22.72s`，跳过项仅为云故障注入 |
| DeepSeek 云连接 | PASS | 使用官方端点和 `deepseek-v4-pro`；真实云套件 `7 passed in 59.37s` |
| DeepSeek 真实 Tool Calling | PASS | 实际执行 LangChain `bind_tools → tool_calls → ToolMessage`；只读工具选择场景覆盖库存、商品、广告等不同意图 |
| A102 DeepSeek Tool Calling | PASS | 精确验证内部销量、广告、内部商品/价格、竞品价格、竞品内容趋势五类工具及参数；最终指标与证据由真实工具结果和 Python 组合器生成 |
| B205 DeepSeek HITL | PASS | 云模型读取库存/商品/销量后由确定性服务创建 `PENDING`；批准前无采购单，有效审批员批准后仅一张 ERP 采购单；重复请求/批准保持幂等 |
| 云故障处理 | PASS | 无效密钥为受控中文 502，极短超时为 504，官方域名真实不可达为 502；前两项各自穿过真实 Streamlit 浏览器并通过，无新增审批、无 traceback、无密钥泄漏 |
| OpenAI 云验证 | 未执行 | 本轮没有使用 OpenAI 凭据，不报告为 PASS；实际验证的云提供商是 DeepSeek |

## 架构与安全证据

- `LLM_PROVIDER=offline` 和 `LLM_PROVIDER=deepseek` 均可运行；Provider 层与业务层分离，两条路径共享 LangChain Tools、ERP/Crawler 客户端、确定性服务和响应契约。
- DeepSeek 配置支持 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`；密钥字段不会出现在 Settings 或 Provider 的 `repr` 中。
- DeepSeek 仅允许官方 HTTPS 主机、无用户信息、标准端口和允许路径；本轮模型为 `deepseek-v4-pro`。
- 云模型只获得当前意图所需的只读工具。Crawler 写操作、批准与 ERP 执行均不暴露给模型。
- A102 的工具名称、商品编码、窗口、竞品编号与关键词由服务端核验；模型自报答案或证据不能替换真实工具结果。
- B205 的补货数量、单价、金额、草稿与幂等键由 Python 业务服务控制。审批仍要求独立审批员凭据，ERP 再验证 `APPROVED`，并由 `approval_id` 唯一约束防止重复采购单。
- `.env` 仍被 Git 忽略；`.env.example` 只有空密钥占位符。运行日志和 Git 历史扫描未发现 DeepSeek 密钥。

## 完整回归

- 本地完整套件：`93 passed, 18 skipped in 26.47s`。18 项均为显式 Compose、云凭据、浏览器或故障注入门控；对应强制路径已在真实环境单独执行。
- 真实 DeepSeek 云套件：`7 passed in 59.37s`，未 mock 云响应。
- DeepSeek Streamlit Playwright：`5 passed, 1 skipped in 48.50s`；浏览器显式断言 DeepSeek 提供商与模型，不能由离线路径假通过。
- Compose API E2E：DeepSeek/最终栈通过；离线切换后 `5 passed in 21.61s`。
- 当前最终源码重建后的 Compose API E2E：`5 passed in 45.23s`。
- 离线 Streamlit Playwright：`5 passed, 1 skipped in 22.72s`。
- 云故障浏览器：无效密钥 `1 passed in 4.37s`；超时 `1 passed in 4.02s`。
- A102 与经营日报云浏览器定向复跑：`1 passed in 17.46s`；三次并发真实日报均返回 200，并各自调用一次销售汇总工具。
- Ruff：PASS。
- 格式检查：61 个文件 PASS。
- Mypy：28 个源文件 PASS。
- `git diff --check`：PASS。
- Docker 最终 `--pull --no-cache` 构建、全栈重建和等待健康完成，耗时 `265.2s`；六个服务均 healthy。
- 最后两项安全边界修复后再次从当前工作树构建并强制重建全栈，耗时 `239.5s`；容器源码哈希与工作树一致。该最终容器的 DeepSeek 浏览器套件为 `5 passed, 1 skipped in 46.97s`。
- 干净数据卷迁移到 `0002_approval_idempotency`；MySQL 16 张表、50 商品、10000 订单。
- 最终运行态恢复为 DeepSeek；实际库存提问返回 200，响应标明 DeepSeek 和 `deepseek-v4-pro`，并调用库存工具。

首次无缓存构建因宿主磁盘空间耗尽和 Docker I/O 失败，未计为 PASS。清理 pip 缓存和 Docker 构建缓存、恢复引擎后，重新执行完整无缓存构建成功；最终记录采用成功重跑证据。

## 独立复审

独立复审曾发现以下 High，并均已修复：

- Provider 默认 `repr` 会暴露 API key。
- A102/B205 只检查工具名而不检查实体参数。
- A102 云路径信任模型答案/证据，并绕过共享 Python 确定性组合器。
- 云浏览器测试未强制提供商，可能由离线路径假通过。
- 云模型创建、工具绑定和工具调用的未知异常可能逸出为未受控 500。
- 超时测试曾允许一般服务错误假冒超时。

最终独立分级：Critical 0，High 0。

## 已知限制

- 未执行 OpenAI 云调用；这不是 DeepSeek 验收阻塞项，也不报告为 PASS。
- 一般化采购意图对“创建”一词仍较宽；误识别最多创建待审批草稿，不能绕过人工审批执行。
- 客户端幂等键尚未绑定请求指纹；恶意跨商品复用同一键会复用旧草稿，服务不会生成重复采购单。
- A102 以外的通用“销量下降”问题仍使用固定竞品映射，尚未建立每个商品的动态竞品关系。
- 云日报最终文案由模型复述，确定性销售汇总工具已强制调用，但文案 evidence 尚未逐字段与汇总结果核验。
- LangGraph 使用进程内 `InMemorySaver`；审批业务状态和恢复镜像持久化在数据库中，重启后重建图状态。
- 浏览器验收通过官方 Playwright Docker 镜像运行；宿主机未单独安装 Chromium。
- Streamlit 框架自带的部分辅助名称仍为英文，业务导航、字段、状态、错误和数据展示已中文化。

## 外部阻塞

无。
