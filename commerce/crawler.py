from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from commerce.config import Settings, get_settings
from commerce.models import (
    CompetitorComment,
    CompetitorContent,
    CompetitorPriceHistory,
    CompetitorProduct,
    CrawlerTask,
    TaskStatus,
    utcnow,
)

logger = logging.getLogger(__name__)


class ProductRecord(BaseModel):
    platform: str = Field(min_length=1, max_length=50)
    external_id: str = Field(min_length=1, max_length=64)
    product_name: str = Field(min_length=1, max_length=200)
    category: str = "未分类"
    price: Decimal = Field(ge=0)
    original_price: Decimal | None = Field(default=None, ge=0)
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    sales: int | None = Field(default=None, ge=0)
    review_count: int = Field(default=0, ge=0)
    url: HttpUrl


class ContentRecord(BaseModel):
    platform: str
    external_id: str
    title: str
    author: str
    publish_time: datetime
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)
    shares: int = Field(ge=0)
    engagement_rate: Decimal = Field(ge=0)
    product_keywords: str = ""
    url: HttpUrl


class CommentRecord(BaseModel):
    platform: str
    external_id: str
    target_id: str
    username: str
    content: str = Field(min_length=1, max_length=2000)
    likes: int = Field(ge=0)
    rating: int | None = Field(default=None, ge=1, le=5)
    publish_time: datetime


def validate_target(url: str, settings: Settings | None = None) -> None:
    config = settings or get_settings()
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("爬虫目标必须是无凭据的 HTTP(S) URL")
    host = parsed.hostname.lower()
    if host in config.crawler_host_allowlist:
        registered = urlparse(config.competitor_base_url)
        allowed_ports = {80, 443}
        if registered.hostname == host:
            allowed_ports.add(registered.port or (443 if registered.scheme == "https" else 80))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in allowed_ports:
            raise ValueError("爬虫目标端口不在允许列表中")
        return
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 80)}
    except socket.gaierror as exc:
        raise ValueError("爬虫目标无法解析") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError("爬虫目标解析到受限网络")
    raise ValueError("爬虫目标域名未在允许列表中")


def parse_product_html(html: str, base_url: str) -> tuple[list[ProductRecord], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for card in soup.select("article.product, article.dynamic-product"):

        def text(selector: str, current_card: Any = card) -> str:
            node = current_card.select_one(selector)
            return node.get_text(strip=True) if node else ""

        link = card.select_one("a")
        records.append(
            ProductRecord(
                platform=card.get("data-platform", "MockMarket"),
                external_id=card.get("data-id", ""),
                product_name=text("h2"),
                category="数码配件",
                price=text(".price").replace("¥", ""),
                original_price=text(".original").replace("¥", "") or None,
                rating=text(".rating") or None,
                sales=int(text(".sales") or 0),
                review_count=int(text(".reviews") or 0),
                url=urljoin(base_url, link.get("href", "")) if link else base_url,
            )
        )
    next_node = soup.select_one("a.next")
    return records, urljoin(base_url, next_node.get("href")) if next_node else None


class CrawlerManager:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def create_task(self, task_type: str, target_url: str) -> CrawlerTask:
        validate_target(target_url, self.settings)
        task = CrawlerTask(task_type=task_type, target_url=target_url, status=TaskStatus.PENDING)
        self.session.add(task)
        self.session.commit()
        return task

    async def run(self, task: CrawlerTask, max_pages: int = 10) -> CrawlerTask:
        task_id = task.id
        task.status, task.started_at, task.error_message = TaskStatus.RUNNING, utcnow(), None
        self.session.commit()
        try:
            if task.task_type == "dynamic":
                records = await self._browser_products(task.target_url)
                count = self._save_products(records)
            elif task.task_type == "products_html":
                records = await self._html_products(task.target_url, max_pages)
                count = self._save_products(records)
            else:
                data = await self._json_pages(task.target_url, max_pages)
                count = self._save_by_type(task.task_type, data)
            task.status, task.records = TaskStatus.SUCCESS, count
        except Exception as exc:
            self.session.rollback()
            refreshed_task = self.session.get(CrawlerTask, task_id)
            if refreshed_task is None:
                raise RuntimeError("爬虫任务状态丢失") from exc
            task = refreshed_task
            logger.exception("crawler_task_failed task_id=%s", task_id)
            task.status, task.error_message = TaskStatus.FAILED, str(exc)[:1000]
        task.finished_at = utcnow()
        self.session.commit()
        return task

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        @retry(
            stop=stop_after_attempt(self.settings.crawler_max_retries),
            wait=wait_exponential(multiplier=0.1, max=1),
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)
            ),
            reraise=True,
        )
        async def request() -> httpx.Response:
            validate_target(url, self.settings)
            await asyncio.sleep(self.settings.crawler_rate_limit_seconds)
            async with client.stream("GET", url) as streamed:
                streamed.raise_for_status()
                content = bytearray()
                async for chunk in streamed.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 5_000_000:
                        raise ValueError("响应超过 5MB 限制")
                return httpx.Response(
                    status_code=streamed.status_code,
                    headers=streamed.headers,
                    content=bytes(content),
                    request=streamed.request,
                )

        return await request()

    async def _json_pages(self, url: str, max_pages: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": "CommerceIntelligenceBot/1.0"},
        ) as client:
            for page in range(1, max_pages + 1):
                response = await self._get(client, url)
                payload = response.json()
                items.extend(payload.get("items", []))
                if not payload.get("has_next"):
                    break
                params = dict(httpx.QueryParams(urlparse(url).query))
                params["page"] = str(page + 1)
                url = str(httpx.URL(url).copy_with(params=params))
                await asyncio.sleep(self.settings.crawler_rate_limit_seconds)
        return items

    async def _html_products(self, url: str, max_pages: int) -> list[ProductRecord]:
        records: list[ProductRecord] = []
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds, follow_redirects=False
        ) as client:
            for _ in range(max_pages):
                response = await self._get(client, url)
                page_records, next_url = parse_product_html(response.text, url)
                records.extend(page_records)
                if not next_url:
                    break
                url = next_url
                await asyncio.sleep(self.settings.crawler_rate_limit_seconds)
        return records

    async def _browser_products(self, url: str) -> list[ProductRecord]:
        from playwright.async_api import async_playwright

        @retry(
            stop=stop_after_attempt(self.settings.crawler_max_retries),
            wait=wait_exponential(multiplier=0.1, max=1),
            reraise=True,
        )
        async def browse() -> str:
            await asyncio.sleep(self.settings.crawler_rate_limit_seconds)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()

                    async def guard(route: Any) -> None:
                        try:
                            validate_target(route.request.url, self.settings)
                        except ValueError:
                            await route.abort()
                        else:
                            await route.continue_()

                    await page.route("**/*", guard)
                    await page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=int(self.settings.request_timeout_seconds * 1000),
                    )
                    validate_target(page.url, self.settings)
                    await page.wait_for_selector(
                        "article.dynamic-product",
                        timeout=int(self.settings.request_timeout_seconds * 1000),
                    )
                    return await page.content()
                finally:
                    await browser.close()

        html = await browse()
        return parse_product_html(html, url)[0]

    def _save_by_type(self, task_type: str, data: list[dict[str, Any]]) -> int:
        if task_type == "products_json":
            return self._save_products([ProductRecord.model_validate(item) for item in data])
        if task_type == "contents":
            return self._save_contents([ContentRecord.model_validate(item) for item in data])
        if task_type == "comments":
            return self._save_comments([CommentRecord.model_validate(item) for item in data])
        raise ValueError(f"不支持的任务类型: {task_type}")

    def _save_products(self, records: list[ProductRecord]) -> int:
        count = 0
        now = utcnow()
        observed_at = datetime(now.year, now.month, now.day, tzinfo=UTC)
        for record in {f"{r.platform}:{r.external_id}": r for r in records}.values():
            current = self.session.scalar(
                select(CompetitorProduct).where(
                    CompetitorProduct.platform == record.platform,
                    CompetitorProduct.external_id == record.external_id,
                )
            )
            values = record.model_dump(mode="python")
            values["url"] = str(values["url"])
            if current:
                for key, value in values.items():
                    setattr(current, key, value)
                current.crawl_time = utcnow()
            else:
                self.session.add(CompetitorProduct(**values))
                count += 1
            daily = self.session.scalar(
                select(CompetitorPriceHistory).where(
                    CompetitorPriceHistory.platform == record.platform,
                    CompetitorPriceHistory.external_id == record.external_id,
                    CompetitorPriceHistory.observed_at == observed_at,
                )
            )
            if daily is None:
                self.session.add(
                    CompetitorPriceHistory(
                        platform=record.platform,
                        external_id=record.external_id,
                        price=record.price,
                        observed_at=observed_at,
                    )
                )
            else:
                daily.price = record.price
        self.session.commit()
        return len(records)

    def _save_contents(self, records: list[ContentRecord]) -> int:
        added = 0
        for record in {f"{r.platform}:{r.external_id}": r for r in records}.values():
            exists = self.session.scalar(
                select(CompetitorContent.id).where(
                    CompetitorContent.platform == record.platform,
                    CompetitorContent.external_id == record.external_id,
                )
            )
            if not exists:
                values = record.model_dump(mode="python")
                values["url"] = str(values["url"])
                self.session.add(CompetitorContent(**values))
                added += 1
        self.session.commit()
        return len(records)

    def _save_comments(self, records: list[CommentRecord]) -> int:
        added = 0
        for record in {f"{r.platform}:{r.external_id}": r for r in records}.values():
            exists = self.session.scalar(
                select(CompetitorComment.id).where(
                    CompetitorComment.platform == record.platform,
                    CompetitorComment.external_id == record.external_id,
                )
            )
            if not exists:
                self.session.add(CompetitorComment(**record.model_dump()))
                added += 1
        self.session.commit()
        return len(records)
