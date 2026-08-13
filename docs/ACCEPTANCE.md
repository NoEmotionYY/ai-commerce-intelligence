# Acceptance Criteria

## Infrastructure

- [ ] Docker images build successfully
- [ ] Docker Compose starts successfully
- [ ] MySQL becomes healthy
- [ ] Agent API becomes healthy
- [ ] Mock ERP becomes healthy
- [ ] Crawler Service becomes healthy
- [ ] Streamlit UI becomes reachable
- [ ] Mock competitor website becomes reachable

## Database

- [ ] Database initializes from a clean state
- [ ] Migrations work
- [ ] Seed data generation works
- [ ] Required business tables exist
- [ ] Required competitor tables exist

## Mock ERP

- [ ] Product query works
- [ ] Order query works
- [ ] Inventory query works
- [ ] Advertising query works
- [ ] Purchase draft works
- [ ] Purchase approval works
- [ ] Approved purchase execution works
- [ ] Rejected purchase does not execute

## Business Intelligence

- [ ] Revenue calculation works
- [ ] Cost calculation works
- [ ] Profit calculation works
- [ ] Profit margin calculation works
- [ ] ROAS calculation works
- [ ] Refund rate calculation works
- [ ] Inventory days calculation works
- [ ] Inventory risk classification works
- [ ] Business anomaly detection works

## Crawler

- [ ] HTTPX crawler works
- [ ] JSON parsing works
- [ ] HTML parsing works
- [ ] Playwright dynamic crawling works
- [ ] Pagination works
- [ ] Retry works
- [ ] Timeout handling works
- [ ] Rate limiting exists
- [ ] Data validation works
- [ ] Deduplication works
- [ ] MySQL persistence works
- [ ] Crawler task status works
- [ ] Failure logging works

## Marketing Intelligence

- [ ] Competitor product analysis works
- [ ] Competitor price comparison works
- [ ] Competitor content analysis works
- [ ] Negative review analysis works
- [ ] Comment topic analysis works

## Agent

- [ ] Natural-language business query works
- [ ] LangChain Tool Calling works
- [ ] Multiple sequential Tool calls work
- [ ] Structured Output works
- [ ] ERP tools work
- [ ] Competitor tools work
- [ ] Crawler tools work
- [ ] Internal and external data can be combined

## LangGraph

- [ ] Workflow state works
- [ ] Routing works
- [ ] Purchase workflow works
- [ ] Human interrupt works
- [ ] Approval works
- [ ] Resume works
- [ ] Reject works
- [ ] Purchase cannot execute before approval

## Key Demo 1

Question:

为什么我们的 A102 最近销量下降？

The answer must actually use:

- internal sales data
- advertising data
- internal price
- competitor price history
- competitor content trend

and generate an evidence-grounded explanation.

## Key Demo 2

Question:

哪些 SKU 未来三天可能缺货？

The system must calculate inventory risk from real seeded data.

## Key Demo 3

Question:

给 B205 创建补货单。

Expected:

Agent
→ inventory analysis
→ purchase recommendation
→ draft
→ human approval
→ ERP execution

No purchase may execute before approval.

## Quality

- [ ] pytest passes
- [ ] lint passes
- [ ] type checking passes where configured
- [ ] Docker build passes
- [ ] Docker smoke tests pass
- [ ] required E2E tests pass
- [ ] no mandatory TODO remains
- [ ] README matches the real project
- [ ] final code review completed
- [ ] all Critical findings fixed
- [ ] all High findings fixed

## Completion

The project is only COMPLETE when every mandatory item above is either:

- PASS

or explicitly documented as blocked by a genuine external dependency.

Incomplete implementation must never be reported as complete.