from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from commerce.crawler import CrawlerManager
from commerce.database import get_session
from commerce.models import CrawlerTask
from commerce.schemas import CrawlerTaskCreate
from commerce.services.marketing import competitor_price_change, content_trend

app = FastAPI(title="Crawler Service", version="0.1.0")


@app.middleware("http")
async def reject_production_runtime(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    from commerce.config import get_settings

    if get_settings().is_production:
        return JSONResponse(
            status_code=410,
            content={"detail": "Legacy Crawler 仅允许在 development/test/demo 运行模式使用"},
        )
    return await call_next(request)


def require_crawler_token(x_crawler_token: str) -> None:
    from commerce.config import get_settings

    configured = get_settings().crawler_service_token
    if not configured:
        raise HTTPException(503, "Crawler 服务令牌未配置")
    if x_crawler_token != configured:
        raise HTTPException(403, "Crawler 服务凭据无效")


def task_dict(task: CrawlerTask) -> dict[str, object]:
    return {
        "id": task.id,
        "task_type": task.task_type,
        "target_url": task.target_url,
        "status": task.status.value,
        "records": task.records,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "error_message": task.error_message,
    }


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": "crawler-service"}


@app.post("/crawler/tasks", status_code=201)
async def create_task(
    payload: CrawlerTaskCreate,
    session: Session = Depends(get_session),
    x_crawler_token: str = Header(default=""),
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    manager = CrawlerManager(session)
    try:
        task = manager.create_task(payload.task_type, str(payload.target_url))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await manager.run(task, payload.max_pages)
    return task_dict(task)


async def _run_named_task(task_type: str, path: str, session: Session) -> dict[str, object]:
    from commerce.config import get_settings

    target = f"{get_settings().competitor_base_url}{path}"
    manager = CrawlerManager(session)
    task = manager.create_task(task_type, target)
    await manager.run(task, 10)
    return task_dict(task)


@app.post("/crawler/products", status_code=201)
async def crawl_products(
    x_crawler_token: str = Header(default=""), session: Session = Depends(get_session)
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    return await _run_named_task("products_json", "/api/products?page=1&page_size=10", session)


@app.post("/crawler/contents", status_code=201)
async def crawl_contents(
    x_crawler_token: str = Header(default=""), session: Session = Depends(get_session)
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    return await _run_named_task("contents", "/api/contents?page=1", session)


@app.post("/crawler/comments", status_code=201)
async def crawl_comments(
    x_crawler_token: str = Header(default=""), session: Session = Depends(get_session)
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    return await _run_named_task("comments", "/api/comments?page=1", session)


@app.post("/crawler/dynamic", status_code=201)
async def crawl_dynamic(
    x_crawler_token: str = Header(default=""), session: Session = Depends(get_session)
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    return await _run_named_task("dynamic", "/dynamic", session)


@app.get("/crawler/tasks")
def tasks(
    x_crawler_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    require_crawler_token(x_crawler_token)
    return [
        task_dict(task)
        for task in session.scalars(select(CrawlerTask).order_by(CrawlerTask.id.desc()))
    ]


@app.get("/crawler/tasks/{task_id}")
def task(
    task_id: int,
    x_crawler_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    result = session.get(CrawlerTask, task_id)
    if result is None:
        raise HTTPException(404, "任务不存在")
    return task_dict(result)


@app.get("/crawler/analysis/prices/{external_id}")
def price_analysis(
    external_id: str,
    as_of: datetime,
    x_crawler_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    return competitor_price_change(session, external_id, as_of)


@app.get("/crawler/analysis/content-trend")
def trend_analysis(
    keyword: str,
    as_of: datetime,
    x_crawler_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_crawler_token(x_crawler_token)
    return content_trend(session, keyword, as_of)
