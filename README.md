# AI 电商运营与营销智能中枢

这是一个可本地运行的 V1/Demo 电商 Agent：以显式 Demo 模式的 Mock ERP 提供内部订单、库存、广告和采购能力，以 HTTPX/Playwright Crawler 采集模拟竞品站，再由单 Agent、LangChain Tools 和 LangGraph 人工审批工作流完成联合分析与业务执行。V2 正在迁移到真实多平台店铺经营助手，V1 Demo 验收不代表 V2 COMPLETE。

## 快速启动

```bash
powershell -File scripts/generate_demo_env.ps1
docker compose up -d --build --wait
```

首次启动必须先生成随机服务、操作员和审批凭据；`.env` 已被 Git 忽略。Linux/macOS
用户可依据 `.env.example` 生成四个至少 32 字节的随机值。生成脚本会把本地演示用的
操作员与审批员凭据加载到界面的密码控件中，但不会明文展示。两种凭据权限独立：
操作员可分析、创建草稿和运行采集，审批员只能批准或拒绝采购申请，二者不可互换。

访问：

- Streamlit：http://localhost:8501
- Agent API 文档：http://localhost:8000/docs
- Mock ERP 文档：http://localhost:8001/docs
- Crawler 文档：http://localhost:8002/docs
- 模拟竞品站：http://localhost:8003/docs

默认本地 Compose 使用显式 `APP_ENV=demo`。离线确定性意图路由的指标来自 Demo 数据库查询和 Python 计算，不依赖外部 LLM 密钥。生产必须设置 `APP_ENV=production` 并配置真实数据源；缺失配置时会返回受控未配置状态，不会 fallback 到 Mock ERP/Crawler 或自动加载 seed。

如需使用 DeepSeek 官方云模型 Tool Calling，在被 Git 忽略的 `.env` 中设置：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的本地密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

仅 `agent-api` 容器会接收 DeepSeek 配置；Streamlit、Crawler、ERP 和模拟站均不会获得云模型密钥。切回 `LLM_PROVIDER=offline` 即恢复离线确定性路由。

## 核心演示

1. 在智能运营助手输入“为什么我们的 A102 最近销量下降？”，响应会展示内部销量、广告、售价，以及外部竞品价格历史和内容热度证据。
2. 输入“哪些 SKU 未来三天可能缺货？”，库存风险由近 7 天真实种子订单计算。
3. 输入“给 B205 创建补货单”，只生成待审批任务；在审批中心批准后，才由受信服务调用内部业务系统创建采购单。
4. 在数据采集中心分别运行商品、内容、评论和动态页面采集，查看任务状态与入库记录数。

## 本地开发

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy commerce
```

真实 Streamlit 浏览器回归需要先启动 Compose，并显式加载 `.env` 中的操作员/审批凭据。宿主机已安装 Chromium 时可直接运行；否则可复用项目的 Playwright 镜像：

```powershell
docker run --rm --network ai-commerce-intelligence_default --env-file .env `
  -e RUN_UI_E2E=1 -e E2E_FRONTEND_URL=http://frontend:8501 `
  -e E2E_AGENT_URL=http://agent-api:8000 -e E2E_ERP_URL=http://mock-erp:8001 `
  -v "${PWD}\tests:/app/tests:ro" ai-commerce-intelligence-frontend `
  bash -lc "pip install --quiet pytest==8.3.5 && python -m pytest tests/e2e/test_streamlit_browser.py -q"
```

数据库迁移与种子：

```bash
alembic upgrade head
# 仅在本地显式 Demo 模式加载固定 fixtures；生产模式不会执行 seed。
APP_ENV=demo python -m scripts.seed
```

## 安全边界

- Agent/LLM 没有任意 SQL 写能力。
- ERP 采购接口要求服务令牌，且会再次验证审批状态和批准内容。
- Crawler 只接受配置允许列表中的 HTTP(S) 目标。
- 工具日志只保存调用摘要，不保存模型私有思维过程或密钥。
- 本项目不实现登录、验证码或访问控制绕过。

详见 [系统架构](docs/ARCHITECTURE.md)、[架构决策](docs/DECISIONS.md) 和 [验收标准](docs/ACCEPTANCE.md)。

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。
