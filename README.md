# AI 电商运营与营销智能中枢

这是一个可本地运行的电商 Agent Demo：以 Mock ERP 提供内部订单、库存、广告和采购能力，以 HTTPX/Playwright Crawler 采集模拟竞品站，再由单 Agent、LangChain Tools 和 LangGraph 人工审批工作流完成联合分析与业务执行。

## 快速启动

```bash
powershell -File scripts/generate_demo_env.ps1
docker compose up -d --build --wait
```

首次启动必须先生成随机服务、操作员和审批凭据；`.env` 已被 Git 忽略。Linux/macOS
用户可依据 `.env.example` 生成四个至少 32 字节的随机值。界面不会内置万能审批密钥，
需从本地 `.env` 手动输入操作员/审批凭据。

访问：

- Streamlit：http://localhost:8501
- Agent API 文档：http://localhost:8000/docs
- Mock ERP 文档：http://localhost:8001/docs
- Crawler 文档：http://localhost:8002/docs
- 模拟竞品站：http://localhost:8003/docs

默认是离线确定性意图路由，所有指标仍来自真实数据库查询和 Python 计算，不依赖外部 LLM 密钥。生产式部署必须替换 `.env.example` 中的演示令牌，并限制服务端口。

## 核心演示

1. 在 AI Copilot 输入“为什么我们的 A102 最近销量下降？”，响应会展示内部销量、广告、售价，以及外部竞品价格历史和内容热度证据。
2. 输入“哪些 SKU 未来三天可能缺货？”，库存风险由近 7 天真实种子订单计算。
3. 输入“给 B205 创建补货单”，只生成待审批任务；在 Approval Center 批准后，才由受信服务调用 Mock ERP 创建采购单。
4. 在 Crawler Center 分别运行商品、内容、评论和动态页面采集，查看任务状态与入库记录数。

## 本地开发

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy commerce
```

数据库迁移与种子：

```bash
alembic upgrade head
python scripts/seed.py
```

## 安全边界

- Agent/LLM 没有任意 SQL 写能力。
- ERP 采购接口要求服务令牌，且会再次验证审批状态和批准内容。
- Crawler 只接受配置允许列表中的 HTTP(S) 目标。
- 工具日志只保存调用摘要，不保存模型私有思维过程或密钥。
- 本项目不实现登录、验证码或访问控制绕过。

详见 [系统架构](docs/ARCHITECTURE.md)、[架构决策](docs/DECISIONS.md) 和 [验收标准](docs/ACCEPTANCE.md)。
