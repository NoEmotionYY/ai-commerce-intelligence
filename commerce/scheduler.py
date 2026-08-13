from __future__ import annotations

import logging

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from commerce.config import get_settings

logger = logging.getLogger(__name__)


def invoke(path: str) -> None:
    settings = get_settings()
    try:
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            client.post(
                f"{settings.crawler_base_url}{path}",
                headers={"X-Crawler-Token": settings.crawler_service_token},
            ).raise_for_status()
    except Exception:
        logger.exception("scheduled_job_failed path=%s", path)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        lambda: invoke("/crawler/products"),
        "cron",
        hour=9,
        id="competitor_prices",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: invoke("/crawler/contents"),
        "cron",
        hour=12,
        id="competitor_contents",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: invoke("/crawler/comments"),
        "cron",
        hour=18,
        id="competitor_comments",
        replace_existing=True,
    )
    return scheduler
