# AI Commerce Operations & Marketing Intelligence Agent

## Document Status

This document defines the product requirements and intended business capabilities
of the project.

It is the primary product specification.

Implementation details may be improved when necessary, but the core business
requirements, demonstration scenarios, security boundaries, and acceptance
criteria must remain satisfied.



# AI 电商运营与营销智能中枢 Demo 完整项目方案

## 一、项目名称

### 中文名称

**AI 电商运营与营销智能中枢**

### 英文名称

**AI Commerce Operations & Marketing Intelligence Agent**

### 项目定位

基于：

**Python + FastAPI + MySQL + LangChain + LangGraph + Playwright + HTTPX + Docker**

构建一个面向电商企业的 AI Agent 系统。

系统同时接入两类数据：

```text
企业内部数据
ERP / OMS / 库存 / 财务 / 广告

+

企业外部数据
竞品商品 / 价格 / 热门内容 / 用户评论
```

通过 Agent 统一完成：

- 订单查询
- 经营分析
- 利润核算
- 库存预警
- 自动补货建议
- 采购审批
- 竞品数据采集
- 竞品价格监控
- 热门内容采集
- 用户评论分析
- 营销机会发现
- 经营异常诊断
- 自动经营日报

最终形成：

```text
数据采集
   ↓
数据治理
   ↓
Agent分析
   ↓
业务决策
   ↓
人工审批
   ↓
企业系统执行
```

------

# 二、项目要解决的问题

假设一家跨境/国内电商公司拥有：

```text
ERP
订单系统
库存系统
广告后台
商品后台
财务Excel
多个竞品平台
```

运营人员每天需要手动完成大量工作。

例如：

```text
查看订单
统计销售额
计算利润
检查库存
检查广告ROI
查看竞品价格
刷竞品视频
看评论
整理差评
判断是否补货
判断是否继续投放
生成日报
```

这些工作的问题是：

```text
系统分散
数据分散
重复操作
大量人工统计
发现问题速度慢
无法形成统一分析
```

本项目通过 AI Agent 建立统一入口。

运营人员只需要问：

> 今天整体经营情况怎么样？

或者：

> 为什么 A102 最近销量下降？

系统自动从：

```text
ERP
广告数据
库存数据
竞品数据
用户评论
```

获取信息，并完成分析。

------

# 三、核心业务价值

整个项目分成两个业务闭环。

## 业务闭环 A：内部运营智能体

```text
ERP
 ↓
订单
 ↓
库存
 ↓
成本
 ↓
广告
 ↓
Agent分析
 ↓
经营判断
 ↓
补货建议
 ↓
人工审批
 ↓
ERP执行
```

主要解决：

- 财务核算
- 订单分析
- 库存管理
- 利润分析
- 采购补货
- 经营日报

------

## 业务闭环 B：营销情报智能体

```text
公开网页/API
 ↓
Crawler
 ↓
商品/内容/评论
 ↓
数据清洗
 ↓
MySQL
 ↓
Marketing Agent
 ↓
竞品分析
 ↓
用户需求分析
 ↓
营销建议
```

主要解决：

- 竞品监控
- 商品价格监控
- 热门内容采集
- 用户评论分析
- 产品痛点发现
- 营销机会发现

------

# 四、最终系统架构

```text
                           用户
                            │
                            ▼
                 ┌───────────────────┐
                 │ Streamlit Web UI  │
                 │                   │
                 │ Dashboard         │
                 │ Agent Chat        │
                 │ Approval Center   │
                 │ Market Intel      │
                 └─────────┬─────────┘
                           │
                           ▼
                 ┌───────────────────┐
                 │      FastAPI      │
                 │                   │
                 │ API Gateway       │
                 └─────────┬─────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │    Agent Orchestrator   │
              │                         │
              │ LangChain + LangGraph   │
              └────────────┬────────────┘
                           │
             ┌─────────────┴──────────────┐
             │                            │
             ▼                            ▼
┌───────────────────────┐      ┌───────────────────────┐
│ Internal Operations   │      │ Marketing Intelligence│
│                       │      │                       │
│ Order Tool            │      │ Crawler Tool          │
│ Inventory Tool        │      │ Competitor Tool       │
│ Finance Tool          │      │ Comment Tool          │
│ Advertising Tool      │      │ Content Tool          │
│ Purchase Tool         │      │ Trend Tool            │
└───────────┬───────────┘      └───────────┬───────────┘
            │                              │
            ▼                              ▼
┌───────────────────────┐      ┌───────────────────────┐
│     ERP Adapter       │      │    Crawler Service    │
│                       │      │                       │
│ HTTPX / REST API      │      │ HTTPX                 │
└───────────┬───────────┘      │ Playwright            │
            │                  │ BeautifulSoup / lxml  │
            ▼                  └───────────┬───────────┘
┌───────────────────────┐                  │
│       Mock ERP        │                  │
│                       │                  │
│ FastAPI               │                  │
│ Orders                │                  │
│ Inventory             │                  │
│ Products              │                  │
│ Purchase              │                  │
└───────────┬───────────┘                  │
            │                              │
            └──────────────┬───────────────┘
                           ▼
                     ┌──────────┐
                     │  MySQL   │
                     └──────────┘
```

------

# 五、技术栈

| 层级              | 技术                           |
| ----------------- | ------------------------------ |
| 开发语言          | Python                         |
| Web 后端          | FastAPI                        |
| 参数校验          | Pydantic                       |
| ORM               | SQLAlchemy                     |
| Agent             | LangChain                      |
| 工作流            | LangGraph                      |
| Tool Calling      | LangChain Tools                |
| Structured Output | Pydantic + LLM                 |
| 数据库            | MySQL                          |
| HTTP采集          | HTTPX                          |
| HTML解析          | BeautifulSoup / lxml           |
| 动态网页          | Playwright                     |
| 前端              | Streamlit                      |
| 调度              | APScheduler，后期可升级 Celery |
| 日志              | Python Logging                 |
| 测试              | Pytest                         |
| 部署              | Docker                         |
| 编排              | Docker Compose                 |
| 版本管理          | Git                            |
| OS                | Linux                          |
| AI Coding         | Codex                          |
| 可选扩展          | OpenClaw / AutoGen             |

------

# 六、项目核心原则

整个项目需要体现四个设计原则。

## 1. LLM 不直接操作数据库

禁止：

```text
LLM
 ↓
生成SQL
 ↓
UPDATE production_table
```

采用：

```text
LLM
 ↓
Tool
 ↓
Business API
 ↓
Service
 ↓
Database
```

------

## 2. 确定性计算不用 LLM

例如：

```text
利润
ROI
ROAS
库存可售天数
退款率
转化率
```

全部使用 Python 计算。

LLM只负责：

```text
理解用户请求
选择工具
组织业务流程
解释结果
生成建议
```

------

## 3. 高风险动作需要审批

读取：

```text
查询订单
查询库存
分析利润
查看竞品
```

可以自动执行。

写入：

```text
创建采购单
修改订单
修改库存
退款
调整广告预算
```

必须：

```text
Agent建议
 ↓
Human Approval
 ↓
ERP执行
```

------

## 4. 爬虫与 Agent 解耦

不要：

```text
LLM直接控制浏览器到处抓数据
```

应该：

```text
Agent
 ↓
Crawler Tool
 ↓
Crawler Service
 ↓
数据清洗
 ↓
数据库
```

Agent主要分析已经结构化的数据。

------

# 七、核心功能模块

系统分成六个主要模块：

```text
01 企业内部运营
02 财务与利润
03 库存与采购
04 竞品爬虫
05 营销情报
06 Agent自动化
```

------

# 八、模块一：订单运营中心

负责：

```text
订单查询
订单统计
SKU销量
退款分析
平台统计
时间趋势
```

支持自然语言：

> 今天有多少订单？

> A102最近7天卖了多少？

> 哪个平台销售额最高？

> 今天退款情况怎么样？

------

## 对应 Tool

```text
get_orders

get_order_detail

get_sales_summary

get_sku_sales

get_refund_summary
```

------

# 九、模块二：财务利润 Agent

主要解决岗位要求中的：

> 财务核算与对账。

利润模型：

```text
销售收入
-
商品成本
-
广告费用
-
平台佣金
-
物流费用
-
退款损失
=
经营利润
```

核心指标：

```text
Revenue

COGS

Gross Profit

Profit Margin

ROI

ROAS

Refund Rate

CAC
```

------

## 示例

用户：

> 为什么 A102 今天利润下降？

系统自动：

```text
Agent
 ↓
get_sku_sales
 ↓
get_product
 ↓
get_advertising_data
 ↓
get_refund_summary
 ↓
calculate_profit
 ↓
compare_with_history
 ↓
LLM解释
```

返回：

```text
A102今日销售额：¥9,826

较7日均值：
-12.8%

广告消耗：
+31.2%

商品成本：
无明显变化

退款率：
6.7% → 8.1%

毛利率：
31.4% → 19.2%

主要原因：
广告成本上涨，同时销量下降。

建议：
暂不继续扩大广告预算。
```

------

# 十、模块三：库存与采购 Agent

主要功能：

```text
库存查询
安全库存
库存预警
库存可售天数
销量预测
补货建议
采购审批
```

------

## 库存计算

```text
available_stock
=
stock
-
reserved_stock
```

最近7日日均销量：

```text
daily_sales
=
7_day_sales / 7
```

库存可售天数：

```text
days_of_stock
=
available_stock / daily_sales
```

------

## 风险等级

```text
>14天
NORMAL

7~14天
ATTENTION

3~7天
WARNING

<3天
CRITICAL
```

------

# 十一、采购 Workflow

用户：

> 给 B205 补货。

流程：

```text
START
 ↓
识别SKU
 ↓
查询库存
 ↓
获取7/14/30天销量
 ↓
计算销售趋势
 ↓
计算建议库存
 ↓
计算补货量
 ↓
查询采购成本
 ↓
生成采购单草稿
 ↓
interrupt
 ↓
等待人工审批
 ↓
      ┌──────────┐
      │          │
   Approve     Reject
      │          │
      ↓          ↓
ERP Purchase    END
API
 ↓
创建采购单
 ↓
写Audit Log
 ↓
END
```

------

# 十二、模块四：Crawler 数据采集中心

这个模块从扩展功能升级为：

# 核心功能。

需要体现：

```text
HTTP采集
动态网页采集
HTML解析
JSON解析
分页
重试
限流
数据清洗
数据库存储
任务状态
```

------

# 十三、Crawler 架构

```text
Agent
 ↓
Crawler Tool
 ↓
Crawler API
 ↓
Crawler Manager
 ↓
          ┌─────────────┐
          │             │
          ▼             ▼
      HTTP Spider   Browser Spider
          │             │
        HTTPX        Playwright
          │             │
          └──────┬──────┘
                 ↓
              Parser
                 ↓
             Cleaner
                 ↓
           Deduplication
                 ↓
              MySQL
```

------

# 十四、两种爬虫模式

## HTTP Spider

优先使用：

```text
HTTPX
```

适合：

```text
公开API
JSON接口
静态HTML
```

需要实现：

```text
Header

Query Params

Pagination

Timeout

Retry

Rate Limit
```

------

## Browser Spider

只有动态网页使用：

```text
Playwright
```

实现：

```text
Chromium

JS Rendering

CSS Selector

XPath

Scroll

Pagination

Wait For Selector
```

面试时解释：

> 能直接使用HTTP获取的数据不会强行使用浏览器，因为HTTP方式资源占用更低；只有依赖JavaScript渲染的页面才使用Playwright。

------

# 十五、爬虫采集的数据

## 商品信息

采集：

```text
商品名称
商品价格
SKU
评分
评价数
销量/销量指标
商品链接
采集时间
平台
```

------

## 内容信息

采集：

```text
标题
作者
发布时间
点赞
评论
分享
互动量
商品关键词
页面地址
```

------

## 评论信息

采集：

```text
评论内容
评论时间
点赞
评分
所属商品/内容
```

------

# 十六、爬虫 Demo 数据源设计

为了防止面试现场：

```text
网站改版
验证码
登录
反爬
网络异常
```

导致 Demo 失败，采用：

## 双数据源模式

### Mode A

```text
Mock Competitor Website
```

自己建立一个模拟竞品站。

保证面试：

**100%可以运行。**

------

### Mode B

```text
Public Website Adapter
```

用于展示真实网页采集能力。

只采集：

- 公开页面
- 无需登录的数据
- 合法授权或允许访问的数据

不把：

```text
绕登录
破解验证码
规避权限
```

作为项目能力点。

------

# 十七、Crawler 数据库设计

增加：

## competitor_products

```text
id

platform

external_id

product_name

category

price

original_price

rating

sales

review_count

url

crawl_time
```

------

## competitor_contents

```text
id

platform

external_id

title

author

publish_time

likes

comments

shares

engagement_rate

url

crawl_time
```

------

## competitor_comments

```text
id

platform

target_id

username

content

likes

rating

publish_time

crawl_time
```

------

## crawler_tasks

```text
id

task_type

target_url

status

started_at

finished_at

records

error_message
```

状态：

```text
PENDING

RUNNING

SUCCESS

FAILED
```

------

# 十八、数据清洗 Pipeline

爬取数据后必须经过：

```text
Raw Data
 ↓
Normalize
 ↓
Validate
 ↓
Deduplicate
 ↓
Clean
 ↓
Save
```

处理：

```text
价格格式

日期格式

空字段

特殊字符

重复数据

异常数值

URL规范化
```

使用 Pydantic 做结构校验。

------

# 十九、模块五：Marketing Intelligence Agent

Crawler负责：

> 获取数据。

Marketing Agent负责：

> 理解数据。

------

## Marketing Agent Tools

增加：

```text
crawl_competitor_products

crawl_competitor_contents

crawl_comments

get_competitor_products

get_competitor_contents

get_competitor_comments

compare_competitor_prices

analyze_comment_topics

analyze_market_trends
```

------

# 二十、竞品价格分析

用户：

> 最近竞品是不是降价了？

Agent：

```text
get_competitor_products
 ↓
读取历史价格
 ↓
Python计算价格变化
 ↓
LLM生成分析
```

输出：

```text
最近7天发现3个主要竞品发生降价。

竞品B：
¥139 → ¥109
下降21.6%

竞品C：
¥129 → ¥119
下降7.8%

其中竞品B降价幅度最大，
同时其互动量增长47%。

建议重点监控竞品B。
```

------

# 二十一、评论分析

用户：

> 分析竞品B最近的差评。

流程：

```text
Agent
 ↓
get_competitor_comments
 ↓
过滤低评分评论
 ↓
Structured LLM Analysis
 ↓
问题分类
 ↓
Python统计
 ↓
生成报告
```

结果：

```text
分析评论：
486条

主要问题：

固定不牢
124条
25.5%

无线充电发热
83条
17.1%

厚手机壳无法充电
57条
11.7%

夹具松动
41条
8.4%
```

Agent进一步生成：

```text
产品机会：

1. 加强固定结构
2. 改善散热
3. 提高厚手机壳兼容性
```

------

# 二十二、内部数据 + 外部数据联合分析

这是整个项目最有价值的功能之一。

用户：

> 为什么我们的 A102 最近销量下降？

Agent自动：

```text
                       问题
                        │
            ┌───────────┴───────────┐
            ↓                       ↓
          内部数据                 外部数据
            │                       │
          ERP                    Crawler
            │                       │
   ┌────────┼────────┐      ┌──────┼───────┐
   ↓        ↓        ↓      ↓      ↓       ↓
 销量      广告      价格   竞品价  热度    评论
   │        │        │      │      │       │
   └────────┴────────┴──────┴──────┴───────┘
                        ↓
                     Agent
                        ↓
                    综合诊断
```

例如：

```text
A102近7日销量：
-23%

广告曝光：
-8%

广告费用：
变化不大

自身售价：
¥129

主要竞品售价：
¥139 → ¥109

竞品相关内容互动：
+47%

竞品新增卖点：
15W快充
```

Agent：

```text
综合判断：

A102销量下降主要并非广告曝光下降造成。

更明显的原因是：

1. 主要竞品价格下降21%
2. 竞品内容曝光增长
3. 新增15W快充卖点

建议：

暂不扩大广告预算。

优先进行价格测试以及素材卖点调整。
```

这个场景就是整个项目的：

# 核心 Demo 场景。

------

# 二十三、自动经营日报

每天自动生成：

```text
Daily Business Intelligence Report
```

内容：

```text
企业经营情况

+

市场变化
```

------

## 示例

```text
2026-08-13 AI经营日报


一、经营概况

订单：
526

销售额：
¥82,630

利润：
¥21,230

毛利率：
25.69%

ROAS：
4.73


二、经营异常

A102
利润率下降至19.2%

B205
预计0.3天缺货

C301
退款率达到31%


三、竞争动态

竞品B：
价格下降21.6%

竞品C：
互动量增长38%

竞品B近7日新增大量
“15W快充”相关内容。


四、用户反馈趋势

竞品主要差评：

固定问题
25%

发热问题
17%

兼容问题
12%


五、AI建议

1. B205建议立即补货
2. A102暂不扩大广告预算
3. 测试A102价格调整
4. 下一代产品优化散热与固定结构
```

------

# 二十四、Agent 架构

第一版本：

# 单 Agent + 多 Tool + LangGraph

而不是一开始使用 Multi-Agent。

```text
Commerce Agent
       │
       ├── Order Tools
       ├── Finance Tools
       ├── Inventory Tools
       ├── Purchase Tools
       ├── Crawler Tools
       └── Marketing Tools
```

LangGraph负责：

```text
State

Workflow

Routing

Approval

Recovery
```

------

# 二十五、为什么不把 AutoGen 放核心

主项目不强依赖 AutoGen。

理由：

```text
简单任务
↓
单Agent + Tools

确定流程
↓
LangGraph

真正需要跨角色协作
↓
Multi-Agent
```

项目成熟后增加 V2：

```text
                    Supervisor
                        │
          ┌─────────────┼─────────────┐
          ↓             ↓             ↓
     Operations      Marketing     Finance
       Agent           Agent        Agent
          │             │             │
          └─────────────┼─────────────┘
                        ↓
                     Report
```

这里可以使用：

```text
AutoGen
```

展示 Multi-Agent 能力。

但不是为了技术栈而强塞。

------

# 二十六、数据库完整设计

最终主要表：

```text
products

orders

order_items

inventory

advertising

purchase_orders

operation_logs

competitor_products

competitor_contents

competitor_comments

crawler_tasks

agent_sessions

approval_tasks
```

------

# 二十七、Approval 数据表

## approval_tasks

```text
id

action_type

action_data

risk_level

status

created_by

approved_by

created_at

approved_at
```

状态：

```text
PENDING

APPROVED

REJECTED

EXECUTED

FAILED
```

------

# 二十八、Operation Log

所有 Tool 调用写日志：

```text
request_id

session_id

tool_name

tool_input

tool_output

duration

status

timestamp
```

这样可以在 Dashboard 显示：

```text
Agent执行记录
```

例如：

```text
10:32:01
get_inventory(B205)
SUCCESS
82ms

10:32:02
get_sku_sales(B205, 7d)
SUCCESS
121ms

10:32:04
create_purchase_draft
SUCCESS
```

注意：

界面展示的是：

> 工具调用和执行记录。

不是模型私有思维过程。

------

# 二十九、项目 API

## Agent

```text
POST /api/chat
```

------

## Dashboard

```text
GET /api/dashboard
```

------

## Business Report

```text
GET /api/reports/daily
```

------

## Inventory

```text
GET /api/inventory/alerts
```

------

## Competitor

```text
GET /api/competitors/products

GET /api/competitors/contents

GET /api/competitors/comments
```

------

## Crawler

```text
POST /api/crawler/tasks

GET /api/crawler/tasks/{id}

GET /api/crawler/tasks
```

------

## Approval

```text
GET /api/approvals

POST /api/approvals/{id}/approve

POST /api/approvals/{id}/reject
```

------

# 三十、Mock ERP API

独立服务：

```text
mock-erp:8001
```

包含：

```text
GET /erp/products

GET /erp/products/{sku}

GET /erp/orders

GET /erp/orders/{order_no}

GET /erp/inventory

GET /erp/inventory/{sku}

GET /erp/advertising

POST /erp/purchase-orders

GET /erp/purchase-orders
```

------

# 三十一、Crawler API

独立服务：

```text
crawler-service:8002
```

接口：

```text
POST /crawler/products

POST /crawler/contents

POST /crawler/comments

GET /crawler/tasks/{id}
```

------

# 三十二、项目目录

```text
ai-commerce-intelligence/
│
├── agent_app/
│   │
│   ├── main.py
│   ├── config.py
│   │
│   ├── agents/
│   │   ├── commerce_agent.py
│   │   ├── prompts.py
│   │   └── state.py
│   │
│   ├── workflows/
│   │   ├── business_analysis.py
│   │   ├── competitor_analysis.py
│   │   ├── purchase_workflow.py
│   │   └── daily_report.py
│   │
│   ├── tools/
│   │   ├── order_tools.py
│   │   ├── inventory_tools.py
│   │   ├── finance_tools.py
│   │   ├── advertising_tools.py
│   │   ├── purchase_tools.py
│   │   ├── crawler_tools.py
│   │   └── competitor_tools.py
│   │
│   ├── services/
│   │   ├── erp_client.py
│   │   ├── crawler_client.py
│   │   ├── finance_service.py
│   │   ├── inventory_service.py
│   │   └── report_service.py
│   │
│   └── api/
│       ├── chat.py
│       ├── dashboard.py
│       ├── approvals.py
│       └── reports.py
│
├── mock_erp/
│   │
│   ├── main.py
│   ├── models/
│   ├── services/
│   ├── database.py
│   └── routers/
│       ├── products.py
│       ├── orders.py
│       ├── inventory.py
│       ├── advertising.py
│       └── purchase.py
│
├── crawler_service/
│   │
│   ├── main.py
│   │
│   ├── spiders/
│   │   ├── base.py
│   │   ├── product_spider.py
│   │   ├── content_spider.py
│   │   └── comment_spider.py
│   │
│   ├── services/
│   │   ├── http_service.py
│   │   ├── browser_service.py
│   │   └── crawler_manager.py
│   │
│   ├── parsers/
│   │   ├── product_parser.py
│   │   ├── content_parser.py
│   │   └── comment_parser.py
│   │
│   ├── pipelines/
│   │   ├── clean.py
│   │   ├── validate.py
│   │   ├── deduplicate.py
│   │   └── save_mysql.py
│   │
│   └── schemas/
│
├── mock_competitor_site/
│
├── frontend/
│   └── streamlit_app.py
│
├── database/
│   ├── models.py
│   └── migrations/
│
├── scripts/
│   ├── init_database.py
│   ├── generate_business_data.py
│   └── generate_competitor_data.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── docs/
│   ├── architecture.md
│   ├── api.md
│   └── demo.md
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── README.md
└── LICENSE
```

------

# 三十三、模拟业务数据

生成：

```text
50个商品SKU

10000条订单

30天广告数据

50条库存数据

50条采购订单
```

故意设置：

```text
A102
广告成本上涨

B205
库存不足

C301
退款率过高

D102
销量异常下降
```

------

# 三十四、模拟竞品数据

准备：

```text
20~50个竞品商品

30天价格历史

300~500条内容

1000~3000条评论
```

故意制造：

```text
竞品B：
最近降价20%

竞品C：
最近内容热度增长

竞品D：
新增热门功能卖点

竞品B：
评论大量出现固定不牢问题
```

使 Agent 能稳定分析出结论。

------

# 三十五、Streamlit 前端

分成四个页面。

## Dashboard

展示：

```text
销售额

订单量

利润

毛利率

ROAS

库存预警

市场异常数量
```

------

## AI Copilot

用户自然语言交互。

------

## Market Intelligence

展示：

```text
竞品价格变化

热门内容

评论热点

市场趋势
```

------

## Approval Center

展示：

```text
待批准采购单

风险操作

Agent执行记录
```

------

# 三十六、Crawler 页面

额外提供：

```text
Crawler Task Center
```

显示：

```text
Task ID

目标

Spider

状态

记录数

耗时
```

例如：

```text
#1021

Competitor B

Product Spider

SUCCESS

42 Records

1.8 sec
```

这样面试官能够直接看到：

> 不是假的爬虫描述，是真正运行的采集任务。

------

# 三十七、Docker 部署

使用：

```text
docker compose up -d
```

启动：

```text
frontend

agent-api

mock-erp

crawler-service

mock-competitor-site

mysql
```

可选：

```text
redis
```

------

# 三十八、测试体系

至少包含：

## Unit Tests

```text
利润计算

ROI计算

ROAS计算

库存计算

异常检测

数据清洗

Parser
```

------

## Integration Tests

```text
ERP API

Crawler API

MySQL

Agent Tools
```

------

## Workflow Tests

```text
采购审批

拒绝采购

失败恢复
```

------

## E2E

测试完整链路：

```text
用户
 ↓
Agent
 ↓
Tool
 ↓
ERP
 ↓
MySQL
 ↓
Response
```

和：

```text
用户
 ↓
Agent
 ↓
Crawler
 ↓
MySQL
 ↓
Marketing Analysis
```

------

# 三十九、异常处理

需要主动设计：

```text
ERP Timeout

Crawler Timeout

Invalid JSON

Selector Missing

Database Error

LLM Error

Tool Error

Approval Expired
```

例如 Crawler失败：

```text
Retry 3 times
 ↓
仍然失败
 ↓
记录FAILED
 ↓
返回可理解错误
```

------

# 四十、Crawler 工程能力

加入：

```text
Timeout

Retry

Rate Limiting

User-Agent

Task Queue

Deduplication

Idempotency

Error Logging
```

重点不是表现“反爬破解”。

重点表现：

> 稳定的数据采集工程能力。

------

# 四十一、定时任务

增加：

```text
APScheduler
```

定时执行：

```text
08:00
生成经营日报

09:00
更新竞品价格

12:00
抓取热门内容

18:00
更新评论

22:00
生成市场情报报告
```

面试 Demo 不需要真实等时间。

提供：

```text
Run Now
```

按钮即可。

------

# 四十二、项目开发阶段

## Phase 1：数据层

完成：

```text
MySQL
数据表
Mock ERP
模拟订单
模拟库存
模拟广告
```

------

## Phase 2：ERP业务层

完成：

```text
订单API
库存API
广告API
采购API
```

------

## Phase 3：经营分析

完成：

```text
利润
库存
异常
日报
```

------

## Phase 4：Crawler

完成：

```text
HTTPX Spider
Playwright Spider
Parser
Pipeline
MySQL
Crawler API
```

------

## Phase 5：Agent

完成：

```text
LangChain
Tools
Structured Output
```

------

## Phase 6：Workflow

完成：

```text
LangGraph
Purchase Approval
State
Interrupt
Resume
```

------

## Phase 7：Marketing Intelligence

完成：

```text
竞品分析
评论分析
价格分析
内外部数据联合分析
```

------

## Phase 8：UI

完成：

```text
Dashboard

Chat

Crawler Center

Market Intelligence

Approval Center
```

------

## Phase 9：工程化

完成：

```text
Docker

Tests

Logging

README

Architecture
```

------

# 四十三、建议开发周期

如果集中开发：

| 时间   | 工作                       |
| ------ | -------------------------- |
| Day 1  | MySQL + Mock ERP           |
| Day 2  | 订单/利润/库存逻辑         |
| Day 3  | HTTPX + Playwright Crawler |
| Day 4  | 数据清洗 + 竞品数据库      |
| Day 5  | LangChain + Tools          |
| Day 6  | LangGraph + Approval       |
| Day 7  | 营销情报 Agent             |
| Day 8  | 内外部联合分析             |
| Day 9  | Streamlit                  |
| Day 10 | Docker + Tests + README    |

------

# 四十四、MVP 范围

如果时间不足，必须保证以下功能完成：

```text
FastAPI

MySQL

Mock ERP

订单查询

库存查询

利润分析

LangChain

Tool Calling

LangGraph

采购审批

HTTPX Crawler

Playwright Crawler

竞品商品采集

竞品评论采集

评论分析

竞品价格分析

ERP + Crawler联合分析

Streamlit

Docker
```

------

# 四十五、不需要优先做

暂时不要做：

```text
Kubernetes

Kafka

Elasticsearch

复杂React

向量数据库

Fine-tuning

复杂RAG

20个Agent

复杂微服务
```

------

# 四十六、面试 Demo 流程

整个演示控制：

# 6~8分钟。

------

## 场景一：经营情况

输入：

> 分析今天经营情况。

Agent调用：

```text
get_sales_summary

get_advertising_data

calculate_profit

analyze_inventory_risk
```

展示：

```text
销售

利润

库存

异常
```

时间：

约1分钟。

------

## 场景二：Crawler

进入：

```text
Crawler Center
```

点击：

```text
抓取竞品
```

展示：

```text
RUNNING
 ↓
SUCCESS
```

然后显示：

```text
42 Products

186 Contents

513 Comments
```

时间：

约1分钟。

------

## 场景三：评论分析

输入：

> 分析竞品B最近的负面评价。

Agent输出：

```text
固定问题：25.5%

发热：17.1%

兼容性：11.7%
```

时间：

约1分钟。

------

# 四十七、最重要的演示场景

输入：

> 为什么我们 A102 最近销量下降？

系统同时调用：

```text
ERP Tools

+

Competitor Tools
```

Agent分析：

```text
内部：

销量 -23%

广告曝光 -8%

自身价格稳定


外部：

竞品B价格下降21%

竞品内容热度 +47%

竞品新增15W快充卖点
```

最终：

```text
判断：

主要问题并非广告流量下降，
而是竞品降价和产品卖点升级。
```

这个场景最能证明：

> 你的 Agent 不只是数据库聊天机器人。

------

# 四十八、最后演示采购自动化

输入：

> B205库存风险怎么样？

系统：

```text
库存：
18

预计：
0.3天售罄

建议：
补货300件
```

继续：

> 创建补货单。

Agent：

```text
Purchase Draft

SKU:
B205

Quantity:
300

Amount:
¥9,600
```

状态：

```text
WAITING APPROVAL
```

点击：

```text
APPROVE
```

然后：

```text
ERP Purchase Order Created

PO-20260813-001
```

完成 Demo。

------

# 四十九、面试时项目介绍

可以用下面的逻辑介绍：

> 这个项目主要模拟电商企业内部的AI运营中枢。我把数据分成内部业务数据和外部市场数据两部分。

> 内部通过自建Mock ERP模拟订单、库存、广告和采购接口，Agent通过Tool Calling访问这些业务API。

> 外部设计了独立Crawler Service，根据数据源分别使用HTTPX和Playwright采集竞品商品、内容和评论，经过清洗和结构化以后存储到MySQL。

> 在Agent层使用LangChain管理Tools，使用LangGraph处理采购等需要保存状态和人工审批的业务Workflow。

> 最核心的能力是把ERP内部经营数据和Crawler获取的市场数据进行联合分析，比如分析一个SKU销量下降时，同时检查内部广告、库存和价格，以及外部竞品价格和内容热度。

> 对于采购这种写操作，我没有允许Agent直接修改数据库，而是经过Human-in-the-loop审批以后，再调用ERP API执行。

------

# 五十、简历项目名称

**AI 电商运营与营销智能中枢**

技术栈：

```text
Python / FastAPI / LangChain / LangGraph /
MySQL / Playwright / HTTPX / Docker
```

项目描述：

> 基于LangChain/LangGraph开发电商运营与营销智能体，通过Tool Calling接入自建ERP REST API及独立Crawler Service，实现订单查询、利润核算、库存预警、采购建议、竞品价格监控和评论分析。

> 基于HTTPX与Playwright搭建数据采集模块，对公开竞品商品、内容及评论数据进行采集、清洗、去重和结构化入库，并实现企业内部经营数据与外部市场数据的联合分析。

> 使用LangGraph构建采购审批状态工作流，通过Human-in-the-loop控制采购等高风险写操作，将LLM决策层与ERP业务执行层解耦。

> 基于FastAPI、MySQL和Docker Compose实现系统服务化与一键部署，并加入Tool调用日志、异常处理和自动化测试。

------

# 五十一、这个项目最终体现的能力

```text
                        你
                         │
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
      Agent            后端             爬虫
        │                │                │
 LangChain          FastAPI           HTTPX
 LangGraph          REST API          Playwright
 Tool Calling       MySQL             HTML/JSON
 Workflow           SQLAlchemy        数据清洗
 HITL               Docker            数据采集
        │                │                │
        └────────────────┼────────────────┘
                         ↓
                    企业业务集成
                         │
               ERP / OMS / Marketing
```

------

# 五十二、最终验收 Checklist

## Backend

-  FastAPI可正常运行
-  MySQL正常连接
-  SQLAlchemy模型完整
-  Mock ERP正常
-  REST API完整
-  参数使用Pydantic校验

## ERP

-  商品查询
-  订单查询
-  库存查询
-  广告查询
-  创建采购单

## Business

-  销售额计算
-  利润计算
-  ROAS计算
-  退款率计算
-  库存风险分析
-  异常检测

## Crawler

-  HTTPX采集
-  Playwright动态采集
-  HTML解析
-  JSON解析
-  分页
-  Retry
-  Timeout
-  数据清洗
-  数据去重
-  MySQL入库
-  Crawler任务状态

## Marketing

-  竞品价格分析
-  热门内容分析
-  评论分析
-  产品痛点分析
-  市场变化分析

## Agent

-  LangChain
-  Tool Calling
-  Structured Output
-  连续多个Tool调用
-  Agent能够调用ERP
-  Agent能够调用Crawler数据
-  Agent支持内外部联合分析

## LangGraph

-  Workflow
-  State
-  Routing
-  Interrupt
-  Resume
-  Human Approval

## Security

-  Agent不能直接修改MySQL
-  写操作通过Tool
-  采购需要审批
-  Tool参数校验
-  操作日志
-  爬虫目标来源受控

## Engineering

-  Pytest
-  Logging
-  Docker
-  Docker Compose
-  .env.example
-  README
-  architecture.md
-  Git提交记录清晰

------

# 五十三、项目最核心的三条链路

最终无论增加多少功能，都必须确保下面三条真正稳定运行。

### 第一条：企业运营

```text
自然语言
 ↓
Agent
 ↓
Tool Calling
 ↓
ERP API
 ↓
MySQL
 ↓
经营分析
```

### 第二条：市场情报

```text
Crawler
 ↓
HTTPX / Playwright
 ↓
数据清洗
 ↓
MySQL
 ↓
Marketing Agent
 ↓
竞品洞察
```

### 第三条：业务执行

```text
Agent决策
 ↓
生成业务操作
 ↓
Human Approval
 ↓
ERP API
 ↓
执行
 ↓
Audit Log
```

只要这三条链真正完成，这个项目就已经不是简单的学生 Agent Demo，而是一套缩小版的：

# 企业 AI Agent 业务系统。

它同时能够证明：

**Agent开发能力 + Python后端能力 + ERP接口能力 + 爬虫能力 + 数据分析能力 + Workflow设计能力 + 工程部署能力。**