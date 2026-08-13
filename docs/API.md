# API 摘要

## Agent API（8000）

- `POST /api/chat`
- `GET /api/dashboard`
- `GET /api/reports/daily`
- `GET /api/inventory/alerts`
- `GET /api/competitors/products`
- `GET /api/competitors/comments/analysis`
- `GET /api/crawler/tasks`
- `GET /api/approvals`
- `POST /api/approvals/{id}/approve`
- `POST /api/approvals/{id}/reject`
- `GET /api/operations`

## Mock ERP（8001）

- 商品、订单、库存、广告查询见 `/docs`。
- `POST /erp/purchase-orders` 仅接受内部服务令牌，且审批必须已批准。

## Crawler（8002）

- `POST /crawler/products|contents|comments|dynamic`
- `POST /crawler/tasks`
- `GET /crawler/tasks`、`GET /crawler/tasks/{id}`

所有服务均提供 `GET /health`。

