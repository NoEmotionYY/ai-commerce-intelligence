from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import (
    AuthorizationError,
    Permission,
    Principal,
    require_permission,
    resolve_shop,
)
from commerce.config import get_settings
from commerce.error_codes import UnsafeErrorCodeError, normalize_error_code
from commerce.logging import SENSITIVE_FIELD_PATTERN
from commerce.models import (
    DataImportRecord,
    DataImportRecordStatus,
    OperationLog,
    PlatformRawEvent,
    RawEventStatus,
    Shop,
    SyncJob,
    SyncJobRawEvent,
    SyncJobStatus,
    utcnow,
)
from commerce.services.shop_connection import (
    ShopConnectionService,
    ShopConnectionUnavailableError,
    ShopConnectionValidationError,
    required_capability_for_job_type,
)

MAX_RAW_PAYLOAD_BYTES = 1_048_576
MAX_CHECKPOINT_BYTES = 65_536
MAX_JSON_DEPTH = 20
CLAIM_LEASE_SECONDS = 300
SENSITIVE_NORMALIZED_KEYS = {
    "auth",
    "bearer",
    "clientsecret",
    "accesstoken",
    "refreshtoken",
    "servicetoken",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "appsecret",
    "apikey",
    "signingkey",
    "encryptionkey",
    "privatekey",
    "xapikey",
}
SENSITIVE_KEY_SUFFIXES = (
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
    "apikey",
    "privatekey",
    "bearer",
    "auth",
)


class IngestionConflictError(ValueError):
    pass


class IngestionNotFoundError(LookupError):
    pass


class IngestionTransitionError(ValueError):
    pass


class IngestionValidationError(ValueError):
    pass


def _canonical_json(
    value: dict[str, object],
    *,
    max_bytes: int,
    label: str,
    allow_pagination_tokens: bool = False,
) -> tuple[dict[str, object], str]:
    if not value:
        raise IngestionValidationError(f"{label}不能为空")

    def validate(item: object, depth: int) -> None:
        if depth > MAX_JSON_DEPTH:
            raise IngestionValidationError(f"{label}嵌套过深")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise IngestionValidationError(f"{label}字段名必须是字符串")
                normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
                is_pagination_token = allow_pagination_tokens and normalized_key in {
                    "pagetoken",
                    "nextpagetoken",
                    "previouspagetoken",
                    "cursortoken",
                    "nextcursortoken",
                }
                if not is_pagination_token and (
                    SENSITIVE_FIELD_PATTERN.search(key)
                    or normalized_key in SENSITIVE_NORMALIZED_KEYS
                    or normalized_key.endswith(SENSITIVE_KEY_SUFFIXES)
                ):
                    raise IngestionValidationError(f"{label}不得包含凭据或密钥字段")
                validate(child, depth + 1)
            return
        if isinstance(item, list):
            for child in item:
                validate(child, depth + 1)
            return
        if item is None or isinstance(item, (str, int, bool)):
            return
        if isinstance(item, float) and math.isfinite(item):
            return
        raise IngestionValidationError(f"{label}包含不支持的 JSON 值")

    validate(value, 0)
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IngestionValidationError(f"{label}不是有效 JSON") from exc
    if len(serialized) > max_bytes:
        raise IngestionValidationError(f"{label}超过大小限制")
    normalized = json.loads(serialized)
    if not isinstance(normalized, dict):
        raise IngestionValidationError(f"{label}必须是对象")
    return normalized, hashlib.sha256(serialized).hexdigest()


def canonical_raw_payload(value: dict[str, object]) -> tuple[dict[str, object], str]:
    """Validate and canonicalize untrusted raw-event evidence before persistence."""
    return _canonical_json(
        value,
        max_bytes=MAX_RAW_PAYLOAD_BYTES,
        label="原始事件 payload",
    )


def _token(value: str, *, label: str, max_length: int) -> str:
    normalized = value.strip().upper()
    if (
        not normalized
        or len(normalized) > max_length
        or re.fullmatch(r"[A-Z0-9][A-Z0-9._:-]*", normalized) is None
    ):
        raise IngestionValidationError(f"{label}无效")
    return normalized


def _claim_hash(claim_token: str) -> str:
    if not 32 <= len(claim_token) <= 256:
        raise IngestionValidationError("处理租约令牌无效")
    return hashlib.sha256(claim_token.encode("utf-8")).hexdigest()


class IngestionService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def _assert_sync_ready(
        self, shop_id: int, capability_code: str, *, for_update: bool = False
    ) -> None:
        try:
            ShopConnectionService(self.session, self.principal).assert_sync_ready(
                shop_id, capability_code, for_update=for_update
            )
        except ShopConnectionUnavailableError as exc:
            raise IngestionTransitionError(str(exc)) from exc

    @staticmethod
    def _required_job_capability(job: SyncJob) -> str:
        try:
            expected = required_capability_for_job_type(job.job_type)
        except ShopConnectionValidationError as exc:
            raise IngestionTransitionError("同步任务能力策略无效") from exc
        if job.required_capability != expected:
            raise IngestionTransitionError("同步任务能力策略不一致")
        return expected

    def _fail_job(self, job: SyncJob, *, error_code: str) -> None:
        if job.status in {
            SyncJobStatus.SUCCESS,
            SyncJobStatus.PARTIAL,
            SyncJobStatus.FAILED,
        }:
            return
        normalized_error = normalize_error_code(error_code)
        job.status = SyncJobStatus.FAILED
        job.last_error = normalized_error
        job.finished_at = utcnow()
        job.lease_token_hash = None
        job.lease_expires_at = None
        self._audit(
            "sync_job.invalidated",
            {
                "sync_job_id": job.id,
                "shop_id": job.shop_id,
                "error_code": normalized_error,
            },
        )

    def _locked_ready_job(
        self, job_id: int, *, expected_shop_id: int | None = None
    ) -> tuple[SyncJob, Shop]:
        snapshot = self._job(job_id)
        if expected_shop_id is not None and snapshot.shop_id != expected_shop_id:
            raise AuthorizationError("同步任务不属于当前店铺")
        shop = resolve_shop(
            self.session,
            self.principal,
            snapshot.shop_id,
            require_active=False,
            for_update=True,
        )
        try:
            expected_capability = required_capability_for_job_type(snapshot.job_type)
        except ShopConnectionValidationError as exc:
            job = self._job(job_id, for_update=True)
            self._fail_job(job, error_code="SYNC_POLICY_MISMATCH")
            self.session.commit()
            raise IngestionTransitionError("同步任务能力策略无效") from exc
        try:
            ShopConnectionService(self.session, self.principal).assert_sync_ready(
                shop.id, expected_capability, for_update=True
            )
        except ShopConnectionUnavailableError as exc:
            job = self._job(job_id, for_update=True)
            self._fail_job(job, error_code=exc.error_code)
            self.session.commit()
            raise IngestionTransitionError(str(exc)) from exc
        job = self._job(job_id, for_update=True)
        if job.shop_id != shop.id or job.job_type != snapshot.job_type:
            self._fail_job(job, error_code="SYNC_POLICY_MISMATCH")
            self.session.commit()
            raise IngestionTransitionError("同步任务能力策略不一致")
        try:
            self._required_job_capability(job)
        except IngestionTransitionError:
            self._fail_job(job, error_code="SYNC_POLICY_MISMATCH")
            self.session.commit()
            raise
        return job, shop

    def create_job(
        self,
        *,
        shop_id: int,
        job_type: str,
        idempotency_key: str,
        max_attempts: int = 3,
        request_fingerprint: str | None = None,
        single_flight: bool = False,
    ) -> SyncJob:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        normalized_type = _token(job_type, label="同步任务类型", max_length=64)
        try:
            required_capability = required_capability_for_job_type(normalized_type)
        except ShopConnectionValidationError as exc:
            raise IngestionValidationError(str(exc)) from exc
        self._assert_sync_ready(shop_id, required_capability, for_update=True)
        shop = resolve_shop(
            self.session,
            self.principal,
            shop_id,
            require_active=True,
            for_update=True,
        )
        raw_key = idempotency_key.strip()
        if not 8 <= len(raw_key) <= 128:
            raise IngestionValidationError("幂等键无效")
        if not 1 <= max_attempts <= 10:
            raise IngestionValidationError("最大重试次数无效")
        if (
            request_fingerprint is not None
            and re.fullmatch(r"[a-f0-9]{64}", request_fingerprint) is None
        ):
            raise IngestionValidationError("同步请求指纹无效")
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        existing = self.session.scalar(
            select(SyncJob).where(
                SyncJob.organization_id == self.principal.organization_id,
                SyncJob.shop_id == shop.id,
                SyncJob.job_type == normalized_type,
                SyncJob.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if (
                existing.idempotency_key == raw_key
                and existing.max_attempts == max_attempts
                and existing.required_capability == required_capability
                and (existing.checkpoint or {}).get("request_fingerprint") == request_fingerprint
            ):
                return existing
            raise IngestionConflictError("同步任务幂等键冲突")
        if single_flight:
            active = self.session.scalar(
                select(SyncJob).where(
                    SyncJob.organization_id == self.principal.organization_id,
                    SyncJob.shop_id == shop.id,
                    SyncJob.job_type == normalized_type,
                    SyncJob.status.in_((SyncJobStatus.PENDING, SyncJobStatus.RUNNING)),
                )
            )
            if active is not None:
                raise IngestionConflictError("同店铺同类型同步任务已在运行")
        job = SyncJob(
            organization_id=self.principal.organization_id,
            shop_id=shop.id,
            job_type=normalized_type,
            required_capability=required_capability,
            idempotency_key=raw_key,
            idempotency_key_hash=key_hash,
            checkpoint=(
                {"request_fingerprint": request_fingerprint}
                if request_fingerprint is not None
                else None
            ),
            max_attempts=max_attempts,
        )
        self.session.add(job)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            concurrent = self.session.scalar(
                select(SyncJob).where(
                    SyncJob.organization_id == self.principal.organization_id,
                    SyncJob.shop_id == shop.id,
                    SyncJob.job_type == normalized_type,
                    SyncJob.idempotency_key_hash == key_hash,
                )
            )
            if (
                concurrent is not None
                and concurrent.idempotency_key == raw_key
                and concurrent.max_attempts == max_attempts
                and concurrent.required_capability == required_capability
                and (concurrent.checkpoint or {}).get("request_fingerprint") == request_fingerprint
            ):
                return concurrent
            raise IngestionConflictError("同步任务已存在") from exc
        self._audit("sync_job.create", {"sync_job_id": job.id, "shop_id": shop.id})
        self.session.commit()
        return job

    def list_jobs(
        self,
        *,
        shop_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
        status: SyncJobStatus | None = None,
    ) -> list[SyncJob]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        if after_id < 0 or not 1 <= limit <= 200:
            raise IngestionValidationError("同步任务分页参数无效")
        statement = select(SyncJob).where(
            SyncJob.organization_id == self.principal.organization_id,
            SyncJob.id > after_id,
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(SyncJob.shop_id == shop_id)
        if status is not None:
            statement = statement.where(SyncJob.status == status)
        return list(self.session.scalars(statement.order_by(SyncJob.id).limit(limit)))

    def get_job(self, job_id: int) -> SyncJob:
        require_permission(self.principal, Permission.READ_COMMERCE)
        return self._job(job_id)

    def start_job(self, job_id: int, *, claim_token: str) -> SyncJob:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        job, _ = self._locked_ready_job(job_id)
        if job.status is SyncJobStatus.RUNNING:
            if self._claim_matches(job.lease_token_hash, claim_token):
                self._require_unexpired(job.lease_expires_at, label="同步任务")
                return job
            raise IngestionTransitionError("同步任务已被其他 Worker 领取")
        if job.status is not SyncJobStatus.PENDING:
            raise IngestionTransitionError("同步任务当前不可启动")
        if job.attempts >= job.max_attempts:
            raise IngestionTransitionError("同步任务已耗尽重试次数")
        now = utcnow()
        claim_hash = _claim_hash(claim_token)
        result = self.session.execute(
            update(SyncJob)
            .where(
                SyncJob.id == job.id,
                SyncJob.organization_id == self.principal.organization_id,
                SyncJob.status == SyncJobStatus.PENDING,
                SyncJob.attempts == job.attempts,
            )
            .values(
                status=SyncJobStatus.RUNNING,
                attempts=job.attempts + 1,
                started_at=now,
                finished_at=None,
                lease_token_hash=claim_hash,
                lease_expires_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.session.rollback()
            current = self._job(job_id)
            if current.status is SyncJobStatus.RUNNING and self._claim_matches(
                current.lease_token_hash, claim_token
            ):
                self._require_unexpired(current.lease_expires_at, label="同步任务")
                return current
            raise IngestionTransitionError("同步任务已被其他 Worker 领取")
        self.session.refresh(job)
        self._audit(
            "sync_job.start",
            {"sync_job_id": job.id, "shop_id": job.shop_id, "attempt": job.attempts},
        )
        self.session.commit()
        return job

    def heartbeat_job(self, job_id: int, *, claim_token: str) -> SyncJob:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        job, _ = self._locked_ready_job(job_id)
        self._require_job_claim(job, claim_token)
        job.lease_expires_at = utcnow() + timedelta(seconds=CLAIM_LEASE_SECONDS)
        self._audit("sync_job.heartbeat", {"sync_job_id": job.id, "shop_id": job.shop_id})
        self.session.commit()
        return job

    def yield_job(self, job_id: int, *, claim_token: str) -> SyncJob:
        """Return a bounded connector chunk to PENDING without consuming retry budget."""
        require_permission(self.principal, Permission.OPERATE_SYNC)
        job, _ = self._locked_ready_job(job_id)
        self._require_job_claim(job, claim_token)
        job.status = SyncJobStatus.PENDING
        job.attempts = max(job.attempts - 1, 0)
        job.lease_token_hash = None
        job.lease_expires_at = None
        self._audit(
            "sync_job.yield",
            {"sync_job_id": job.id, "shop_id": job.shop_id},
        )
        self.session.commit()
        return job

    def update_checkpoint(
        self, job_id: int, checkpoint: dict[str, object], *, claim_token: str
    ) -> SyncJob:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        job, _ = self._locked_ready_job(job_id)
        self._require_job_claim(job, claim_token)
        normalized, _ = _canonical_json(
            checkpoint,
            max_bytes=MAX_CHECKPOINT_BYTES,
            label="同步 checkpoint",
            allow_pagination_tokens=True,
        )
        existing_fingerprint = (job.checkpoint or {}).get("request_fingerprint")
        incoming_fingerprint = normalized.get("request_fingerprint")
        if existing_fingerprint is not None:
            if incoming_fingerprint not in {None, existing_fingerprint}:
                raise IngestionConflictError("同步 checkpoint 请求指纹冲突")
            normalized["request_fingerprint"] = existing_fingerprint
        job.checkpoint = normalized
        self._audit("sync_job.checkpoint", {"sync_job_id": job.id, "shop_id": job.shop_id})
        self.session.commit()
        return job

    def finish_job(
        self,
        job_id: int,
        *,
        status: SyncJobStatus,
        claim_token: str,
        error_code: str | None = None,
    ) -> SyncJob:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        try:
            normalized_error = normalize_error_code(error_code) if error_code else None
        except UnsafeErrorCodeError as exc:
            raise IngestionValidationError(str(exc)) from exc
        if status is SyncJobStatus.SUCCESS and normalized_error is not None:
            raise IngestionValidationError("成功的同步任务不得包含 error_code")
        snapshot = self._job(job_id)
        if snapshot.status is SyncJobStatus.RUNNING:
            job, _ = self._locked_ready_job(job_id)
        else:
            job = self._job(job_id, for_update=True)
        self._require_job_claim(job, claim_token, allow_terminal=True)
        if job.status is status and job.status in {
            SyncJobStatus.SUCCESS,
            SyncJobStatus.PARTIAL,
            SyncJobStatus.FAILED,
        }:
            if job.last_error == normalized_error:
                return job
            raise IngestionTransitionError("同步任务已以不同结果结束")
        if job.status is not SyncJobStatus.RUNNING:
            raise IngestionTransitionError("仅运行中的同步任务可以结束")
        if status not in {SyncJobStatus.SUCCESS, SyncJobStatus.PARTIAL, SyncJobStatus.FAILED}:
            raise IngestionTransitionError("同步任务结束状态无效")
        if status is SyncJobStatus.SUCCESS:
            unresolved = self.session.scalar(
                select(func.count())
                .select_from(SyncJobRawEvent)
                .where(
                    SyncJobRawEvent.sync_job_id == job.id,
                    SyncJobRawEvent.processed_at.is_(None),
                )
            )
            if unresolved:
                raise IngestionTransitionError("同步任务仍有未完成的原始事件")
        if status in {SyncJobStatus.PARTIAL, SyncJobStatus.FAILED} and not error_code:
            raise IngestionValidationError("失败或部分成功必须提供 error_code")
        job.status = status
        job.last_error = normalized_error
        job.finished_at = utcnow()
        self._audit(
            "sync_job.finish",
            {
                "sync_job_id": job.id,
                "shop_id": job.shop_id,
                "status": job.status.value,
                "error_code": job.last_error,
            },
        )
        self.session.commit()
        return job

    def retry_job(self, job_id: int) -> SyncJob:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        job, _ = self._locked_ready_job(job_id)
        if job.status is SyncJobStatus.PENDING and job.attempts > 0 and job.last_error:
            return job
        if job.status not in {SyncJobStatus.PARTIAL, SyncJobStatus.FAILED}:
            raise IngestionTransitionError("同步任务当前不可重试")
        if job.attempts >= job.max_attempts:
            raise IngestionTransitionError("同步任务已耗尽重试次数")
        previous_status = job.status
        job.status = SyncJobStatus.PENDING
        job.finished_at = None
        job.lease_token_hash = None
        job.lease_expires_at = None
        self._audit(
            "sync_job.retry",
            {
                "sync_job_id": job.id,
                "shop_id": job.shop_id,
                "previous_status": previous_status.value,
                "next_attempt": job.attempts + 1,
            },
        )
        self.session.commit()
        return job

    def recover_expired_job(self, job_id: int) -> SyncJob:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        job, _ = self._locked_ready_job(job_id)
        if (
            job.status is SyncJobStatus.PENDING
            and job.last_error == "WORKER_LEASE_EXPIRED"
            and job.lease_token_hash is None
        ):
            return job
        if job.status is not SyncJobStatus.RUNNING:
            raise IngestionTransitionError("仅运行中的同步任务可以恢复")
        if job.lease_expires_at is None or job.lease_expires_at > utcnow():
            raise IngestionTransitionError("同步任务租约尚未过期")
        job.last_error = "WORKER_LEASE_EXPIRED"
        job.lease_token_hash = None
        job.lease_expires_at = None
        if job.attempts >= job.max_attempts:
            job.status = SyncJobStatus.FAILED
            job.finished_at = utcnow()
        else:
            job.status = SyncJobStatus.PENDING
            job.finished_at = None
        self._audit(
            "sync_job.recover",
            {"sync_job_id": job.id, "shop_id": job.shop_id, "status": job.status.value},
        )
        self.session.commit()
        return job

    def ingest_event(
        self,
        *,
        shop_id: int,
        sync_job_id: int,
        event_type: str,
        external_event_id: str,
        payload: dict[str, object],
        occurred_at: datetime | None,
        sync_job_claim_token: str,
    ) -> PlatformRawEvent:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        job, shop = self._locked_ready_job(sync_job_id, expected_shop_id=shop_id)
        self._require_job_claim(job, sync_job_claim_token)
        return self._ingest_event(
            shop=shop,
            job=job,
            sync_job_claim_token=sync_job_claim_token,
            event_type=event_type,
            external_event_id=external_event_id,
            payload=payload,
            occurred_at=occurred_at,
        )

    def ingest_fixture_event(
        self,
        *,
        shop_id: int,
        event_type: str,
        external_event_id: str,
        payload: dict[str, object],
        occurred_at: datetime | None,
    ) -> PlatformRawEvent:
        if not get_settings().allows_fixtures:
            raise IngestionTransitionError("当前运行模式不允许写入 fixture 原始事件")
        require_permission(self.principal, Permission.OPERATE_SYNC)
        shop = resolve_shop(self.session, self.principal, shop_id)
        return self._ingest_event(
            shop=shop,
            job=None,
            sync_job_claim_token=None,
            event_type=event_type,
            external_event_id=external_event_id,
            payload=payload,
            occurred_at=occurred_at,
        )

    def ingest_import_event(
        self,
        *,
        import_record_id: int,
        event_type: str,
        external_event_id: str,
        payload: dict[str, object],
        occurred_at: datetime | None,
    ) -> PlatformRawEvent:
        """Persist file-import evidence without pretending it came from a platform connector."""
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        record = self._import_record(import_record_id, for_update=True)
        shop = resolve_shop(self.session, self.principal, record.shop_id)
        if record.raw_event_id is not None:
            event = self._import_event(record, record.raw_event_id)
            normalized_payload, payload_hash = _canonical_json(
                payload, max_bytes=MAX_RAW_PAYLOAD_BYTES, label="原始事件 payload"
            )
            del normalized_payload
            if (
                event.event_type == _token(event_type, label="事件类型", max_length=100)
                and event.external_event_id == external_event_id.strip()
                and event.payload_hash == payload_hash
            ):
                return event
            raise IngestionConflictError("导入记录已绑定不同的原始事件")
        event = self._ingest_event(
            shop=shop,
            job=None,
            sync_job_claim_token=None,
            event_type=event_type,
            external_event_id=external_event_id,
            payload=payload,
            occurred_at=occurred_at,
        )
        record = self._import_record(import_record_id, for_update=True)
        if record.raw_event_id is not None and record.raw_event_id != event.id:
            raise IngestionConflictError("导入记录已被并发绑定到其他原始事件")
        record.raw_event_id = event.id
        self._audit(
            "raw_event.import_link",
            {"raw_event_id": event.id, "import_record_id": record.id, "shop_id": record.shop_id},
        )
        self.session.commit()
        return event

    def reject_import_event(self, import_record_id: int, *, error_code: str) -> PlatformRawEvent:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        record = self._import_record(import_record_id, for_update=True)
        if record.raw_event_id is None:
            raise IngestionTransitionError("导入记录尚未绑定原始事件")
        event = self._import_event(record, record.raw_event_id, for_update=True)
        if event.status is RawEventStatus.FAILED and event.last_error == error_code:
            return event
        if event.status is not RawEventStatus.RECEIVED:
            raise IngestionTransitionError("仅未处理的导入原始事件可以拒绝")
        event.status = RawEventStatus.FAILED
        event.last_error = normalize_error_code(error_code)
        self._audit(
            "raw_event.import_reject",
            {"raw_event_id": event.id, "import_record_id": record.id, "shop_id": record.shop_id},
        )
        self.session.commit()
        return event

    def begin_import_event(self, import_record_id: int, *, claim_token: str) -> PlatformRawEvent:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        record = self._import_record(import_record_id, for_update=True)
        if record.status not in {
            DataImportRecordStatus.VALID,
            DataImportRecordStatus.PROCESSING,
        }:
            raise IngestionTransitionError("导入记录当前不可开始处理")
        if record.raw_event_id is None:
            raise IngestionTransitionError("导入记录尚未绑定原始事件")
        event = self._import_event(record, record.raw_event_id, for_update=True)
        if event.status is RawEventStatus.PROCESSING:
            if self._claim_matches(event.processing_token_hash, claim_token):
                self._require_unexpired(event.processing_lease_expires_at, label="导入原始事件")
                return event
            raise IngestionTransitionError("导入原始事件已被其他执行者领取")
        if event.status is not RawEventStatus.RECEIVED:
            raise IngestionTransitionError("导入原始事件当前不可开始处理")
        event.status = RawEventStatus.PROCESSING
        event.processing_attempts += 1
        event.processing_token_hash = _claim_hash(claim_token)
        event.processing_lease_expires_at = utcnow() + timedelta(seconds=CLAIM_LEASE_SECONDS)
        record.status = DataImportRecordStatus.PROCESSING
        self._audit(
            "raw_event.import_begin",
            {"raw_event_id": event.id, "import_record_id": record.id, "shop_id": record.shop_id},
        )
        self.session.commit()
        return event

    def lock_import_event(
        self,
        import_record_id: int,
        event_id: int,
        *,
        claim_token: str,
        allow_processed: bool = False,
    ) -> PlatformRawEvent:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        record = self._import_record(import_record_id, for_update=True)
        event = self._import_event(record, event_id, for_update=True)
        self._verify_event_evidence(event)
        self._require_event_claim(event, claim_token, allow_terminal=allow_processed)
        return event

    def complete_import_event(self, import_record_id: int, *, claim_token: str) -> PlatformRawEvent:
        record = self._import_record(import_record_id, for_update=True)
        if record.raw_event_id is None:
            raise IngestionTransitionError("导入记录尚未绑定原始事件")
        event = self.lock_import_event(
            record.id, record.raw_event_id, claim_token=claim_token, allow_processed=True
        )
        self.stage_event_completion(event, claim_token=claim_token)
        self.session.commit()
        return event

    def fail_import_event(
        self, import_record_id: int, *, claim_token: str, error_code: str
    ) -> PlatformRawEvent:
        record = self._import_record(import_record_id, for_update=True)
        if record.raw_event_id is None:
            raise IngestionTransitionError("导入记录尚未绑定原始事件")
        event = self.lock_import_event(
            record.id, record.raw_event_id, claim_token=claim_token, allow_processed=True
        )
        normalized_error = normalize_error_code(error_code)
        if event.status is RawEventStatus.FAILED:
            if event.last_error == normalized_error:
                return event
            raise IngestionTransitionError("导入原始事件已以不同错误失败")
        if event.status is not RawEventStatus.PROCESSING:
            raise IngestionTransitionError("仅处理中的导入原始事件可以失败")
        event.status = RawEventStatus.FAILED
        event.last_error = normalized_error
        event.processed_at = None
        self._audit(
            "raw_event.import_fail",
            {"raw_event_id": event.id, "import_record_id": record.id, "shop_id": record.shop_id},
        )
        self.session.commit()
        return event

    def retry_import_event(self, import_record_id: int) -> PlatformRawEvent:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        record = self._import_record(import_record_id, for_update=True)
        if record.raw_event_id is None:
            raise IngestionTransitionError("导入记录尚未绑定原始事件")
        event = self._import_event(record, record.raw_event_id, for_update=True)
        if (
            record.status is DataImportRecordStatus.VALID
            and event.status is RawEventStatus.RECEIVED
        ):
            return event
        retryable_failure = (
            record.status is DataImportRecordStatus.FAILED
            and event.status is RawEventStatus.FAILED
            and event.last_error == "IMPORT_EXECUTION_FAILED"
        )
        expired_processing = (
            record.status in {DataImportRecordStatus.FAILED, DataImportRecordStatus.PROCESSING}
            and event.status is RawEventStatus.PROCESSING
            and event.processing_lease_expires_at is not None
            and event.processing_lease_expires_at <= utcnow()
        )
        if not retryable_failure and not expired_processing:
            raise IngestionTransitionError("导入记录当前不可重试")
        previous_status = event.status
        event.status = RawEventStatus.RECEIVED
        event.last_error = None
        event.processed_at = None
        event.processing_token_hash = None
        event.processing_lease_expires_at = None
        record.status = DataImportRecordStatus.VALID
        record.result = None
        record.error_code = None
        record.errors = []
        self._audit(
            "raw_event.import_retry",
            {
                "raw_event_id": event.id,
                "import_record_id": record.id,
                "shop_id": record.shop_id,
                "previous_status": previous_status.value,
            },
        )
        self.session.commit()
        return event

    def _ingest_event(
        self,
        *,
        shop: Shop,
        job: SyncJob | None,
        sync_job_claim_token: str | None,
        event_type: str,
        external_event_id: str,
        payload: dict[str, object],
        occurred_at: datetime | None,
    ) -> PlatformRawEvent:
        normalized_type = _token(event_type, label="事件类型", max_length=100)
        external_id = external_event_id.strip()
        if not external_id or len(external_id) > 256:
            raise IngestionValidationError("外部事件 ID 无效")
        if occurred_at is not None and occurred_at.tzinfo is None:
            raise IngestionValidationError("事件发生时间必须包含时区")
        if occurred_at is not None:
            occurred_at = occurred_at.astimezone(UTC)
        normalized_payload, payload_hash = canonical_raw_payload(payload)
        identity = f"{normalized_type}\0{external_id}".encode()
        source_key = hashlib.sha256(identity).hexdigest()
        existing = self.session.scalar(
            select(PlatformRawEvent).where(
                PlatformRawEvent.organization_id == self.principal.organization_id,
                PlatformRawEvent.shop_id == shop.id,
                PlatformRawEvent.source_event_key == source_key,
            )
        )
        if existing is not None:
            if existing.external_event_id == external_id and existing.payload_hash == payload_hash:
                if job is not None:
                    self._require_job_claim(job, sync_job_claim_token or "")
                    self._observe_event(job, existing)
                    self.session.commit()
                return existing
            raise IngestionConflictError("外部事件 ID 已存在但 payload 不一致")
        if job is not None:
            self._require_job_claim(job, sync_job_claim_token or "")
        event = PlatformRawEvent(
            organization_id=self.principal.organization_id,
            shop_id=shop.id,
            platform=shop.platform,
            event_type=normalized_type,
            external_event_id=external_id,
            source_event_key=source_key,
            payload=normalized_payload,
            payload_hash=payload_hash,
            occurred_at=occurred_at,
        )
        self.session.add(event)
        try:
            self.session.flush()
            if job is not None:
                self._observe_event(job, event)
        except IntegrityError as exc:
            self.session.rollback()
            concurrent = self.session.scalar(
                select(PlatformRawEvent).where(
                    PlatformRawEvent.organization_id == self.principal.organization_id,
                    PlatformRawEvent.shop_id == shop.id,
                    PlatformRawEvent.source_event_key == source_key,
                )
            )
            if (
                concurrent is not None
                and concurrent.external_event_id == external_id
                and concurrent.payload_hash == payload_hash
            ):
                if job is not None:
                    locked_job, _ = self._locked_ready_job(job.id, expected_shop_id=shop.id)
                    self._require_job_claim(locked_job, sync_job_claim_token or "")
                    self._observe_event(locked_job, concurrent)
                    self.session.commit()
                return concurrent
            raise IngestionConflictError("外部事件已存在") from exc
        self._audit(
            "raw_event.ingest",
            {
                "raw_event_id": event.id,
                "shop_id": shop.id,
                "sync_job_id": job.id if job is not None else None,
                "event_type": normalized_type,
                "payload_hash": payload_hash,
            },
        )
        self.session.commit()
        return event

    def list_events(
        self,
        *,
        shop_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
        status: RawEventStatus | None = None,
    ) -> list[PlatformRawEvent]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        if after_id < 0 or not 1 <= limit <= 200:
            raise IngestionValidationError("原始事件分页参数无效")
        statement = select(PlatformRawEvent).where(
            PlatformRawEvent.organization_id == self.principal.organization_id,
            PlatformRawEvent.id > after_id,
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(PlatformRawEvent.shop_id == shop_id)
        if status is not None:
            statement = statement.where(PlatformRawEvent.status == status)
        return list(self.session.scalars(statement.order_by(PlatformRawEvent.id).limit(limit)))

    def get_event(self, event_id: int) -> PlatformRawEvent:
        require_permission(self.principal, Permission.READ_RAW_EVENTS)
        event = self.session.scalar(
            select(PlatformRawEvent).where(
                PlatformRawEvent.id == event_id,
                PlatformRawEvent.organization_id == self.principal.organization_id,
            )
        )
        if event is None:
            raise IngestionNotFoundError("原始事件不存在")
        self._audit(
            "raw_event.read",
            {
                "raw_event_id": event.id,
                "shop_id": event.shop_id,
                "payload_hash": event.payload_hash,
            },
        )
        return event

    def begin_event(
        self,
        event_id: int,
        *,
        claim_token: str,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> PlatformRawEvent:
        event = self._event_for_processing(
            event_id,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
        )
        self._verify_event_evidence(event)
        if event.status is RawEventStatus.PROCESSING:
            if self._claim_matches(event.processing_token_hash, claim_token):
                self._require_unexpired(event.processing_lease_expires_at, label="原始事件")
                return event
            raise IngestionTransitionError("原始事件已被其他 Worker 领取")
        if event.status is not RawEventStatus.RECEIVED:
            raise IngestionTransitionError("原始事件当前不可开始处理")
        now = utcnow()
        claim_hash = _claim_hash(claim_token)
        result = self.session.execute(
            update(PlatformRawEvent)
            .where(
                PlatformRawEvent.id == event.id,
                PlatformRawEvent.organization_id == self.principal.organization_id,
                PlatformRawEvent.status == RawEventStatus.RECEIVED,
                PlatformRawEvent.processing_attempts == event.processing_attempts,
            )
            .values(
                status=RawEventStatus.PROCESSING,
                processing_attempts=event.processing_attempts + 1,
                processing_token_hash=claim_hash,
                processing_lease_expires_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.session.rollback()
            current = self._event_for_write(event_id)
            if current.status is RawEventStatus.PROCESSING and self._claim_matches(
                current.processing_token_hash, claim_token
            ):
                self._require_unexpired(current.processing_lease_expires_at, label="原始事件")
                return current
            raise IngestionTransitionError("原始事件已被其他 Worker 领取")
        self.session.refresh(event)
        self._audit("raw_event.begin", {"raw_event_id": event.id, "shop_id": event.shop_id})
        self.session.commit()
        return event

    def heartbeat_event(
        self,
        event_id: int,
        *,
        claim_token: str,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> PlatformRawEvent:
        event = self._event_for_processing(
            event_id,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
        )
        self._require_event_claim(event, claim_token)
        event.processing_lease_expires_at = utcnow() + timedelta(seconds=CLAIM_LEASE_SECONDS)
        self._audit("raw_event.heartbeat", {"raw_event_id": event.id, "shop_id": event.shop_id})
        self.session.commit()
        return event

    def complete_event(
        self,
        event_id: int,
        *,
        claim_token: str,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> PlatformRawEvent:
        event = self.lock_claimed_event(
            event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            allow_processed=True,
        )
        self.stage_event_completion(event, claim_token=claim_token)
        self.session.commit()
        return event

    def lock_claimed_event(
        self,
        event_id: int,
        *,
        claim_token: str,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
        allow_processed: bool = False,
    ) -> PlatformRawEvent:
        event = self._event_for_processing(
            event_id,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
        )
        self._verify_event_evidence(event)
        self._require_event_claim(event, claim_token, allow_terminal=allow_processed)
        return event

    def stage_event_completion(
        self, event: PlatformRawEvent, *, claim_token: str
    ) -> PlatformRawEvent:
        self._require_event_claim(event, claim_token, allow_terminal=True)
        if event.status is RawEventStatus.PROCESSED:
            return event
        if event.status is not RawEventStatus.PROCESSING:
            raise IngestionTransitionError("仅处理中的原始事件可以完成")
        event.status = RawEventStatus.PROCESSED
        completed_at = utcnow()
        event.processed_at = completed_at
        event.last_error = None
        self.session.execute(
            update(SyncJobRawEvent)
            .where(
                SyncJobRawEvent.raw_event_id == event.id,
                SyncJobRawEvent.processed_at.is_(None),
            )
            .values(processed_at=completed_at)
            .execution_options(synchronize_session=False)
        )
        self._audit("raw_event.complete", {"raw_event_id": event.id, "shop_id": event.shop_id})
        return event

    def fail_event(
        self,
        event_id: int,
        *,
        error_code: str,
        claim_token: str,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> PlatformRawEvent:
        event = self._event_for_processing(
            event_id,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
        )
        try:
            normalized_error = normalize_error_code(error_code)
        except UnsafeErrorCodeError as exc:
            raise IngestionValidationError(str(exc)) from exc
        self._require_event_claim(event, claim_token, allow_terminal=True)
        if event.status is RawEventStatus.FAILED:
            if event.last_error == normalized_error:
                return event
            raise IngestionTransitionError("原始事件已以不同错误失败")
        if event.status is not RawEventStatus.PROCESSING:
            raise IngestionTransitionError("仅处理中的原始事件可以失败")
        event.status = RawEventStatus.FAILED
        event.last_error = normalized_error
        event.processed_at = None
        self._audit(
            "raw_event.fail",
            {
                "raw_event_id": event.id,
                "shop_id": event.shop_id,
                "error_code": event.last_error,
            },
        )
        self.session.commit()
        return event

    def recover_expired_event(self, event_id: int) -> PlatformRawEvent:
        event = self._event_for_write(event_id)
        if (
            event.status is RawEventStatus.FAILED
            and event.last_error == "WORKER_LEASE_EXPIRED"
            and event.processing_token_hash is None
        ):
            return event
        if event.status is not RawEventStatus.PROCESSING:
            raise IngestionTransitionError("仅处理中的原始事件可以恢复")
        if (
            event.processing_lease_expires_at is None
            or event.processing_lease_expires_at > utcnow()
        ):
            raise IngestionTransitionError("原始事件租约尚未过期")
        event.status = RawEventStatus.FAILED
        event.last_error = "WORKER_LEASE_EXPIRED"
        event.processing_token_hash = None
        event.processing_lease_expires_at = None
        self._audit("raw_event.recover", {"raw_event_id": event.id, "shop_id": event.shop_id})
        self.session.commit()
        return event

    def replay_event(
        self,
        event_id: int,
        *,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> PlatformRawEvent:
        if (sync_job_id is None) != (sync_job_claim_token is None):
            raise IngestionValidationError("原始事件重放必须同时提供同步任务和任务租约")
        if sync_job_id is None:
            event = self._event_for_write(event_id)
            observation_count = self.session.scalar(
                select(func.count())
                .select_from(SyncJobRawEvent)
                .where(SyncJobRawEvent.raw_event_id == event.id)
            )
            if observation_count and not self._has_successful_observation(event.id):
                raise IngestionValidationError("原始事件重放必须绑定可信同步任务")
            if not observation_count and not get_settings().allows_fixtures:
                raise IngestionValidationError("原始事件重放必须绑定可信同步任务")
        else:
            event = self._event_for_processing(
                event_id,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                require_observation=False,
            )
        self._verify_event_evidence(event)
        if event.status is RawEventStatus.RECEIVED and event.replay_count > 0:
            return event
        if event.status not in {RawEventStatus.PROCESSED, RawEventStatus.FAILED}:
            raise IngestionTransitionError("原始事件当前不可重放")
        has_successful_observation = self._has_successful_observation(event.id)
        if sync_job_id is not None:
            job = self._job(sync_job_id, for_update=True)
            self._observe_event(job, event, requires_processing=True)
        elif has_successful_observation:
            raise IngestionTransitionError("成功任务的原始事件必须绑定新的运行任务后重放")
        previous_status = event.status
        event.status = RawEventStatus.RECEIVED
        event.replay_count += 1
        event.processed_at = None
        event.processing_token_hash = None
        event.processing_lease_expires_at = None
        self._audit(
            "raw_event.replay",
            {
                "raw_event_id": event.id,
                "shop_id": event.shop_id,
                "previous_status": previous_status.value,
                "payload_hash": event.payload_hash,
            },
        )
        self.session.commit()
        return event

    def _has_successful_observation(self, event_id: int) -> bool:
        count = self.session.scalar(
            select(func.count())
            .select_from(SyncJobRawEvent)
            .join(SyncJob, SyncJob.id == SyncJobRawEvent.sync_job_id)
            .where(
                SyncJobRawEvent.raw_event_id == event_id,
                SyncJob.status == SyncJobStatus.SUCCESS,
            )
        )
        return bool(count)

    def _job(self, job_id: int, *, for_update: bool = False) -> SyncJob:
        statement = select(SyncJob).where(
            SyncJob.id == job_id,
            SyncJob.organization_id == self.principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        job = self.session.scalar(statement)
        if job is None:
            raise IngestionNotFoundError("同步任务不存在")
        return job

    def _event(self, event_id: int, *, for_update: bool = False) -> PlatformRawEvent:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        statement = select(PlatformRawEvent).where(
            PlatformRawEvent.id == event_id,
            PlatformRawEvent.organization_id == self.principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        event = self.session.scalar(statement)
        if event is None:
            raise IngestionNotFoundError("原始事件不存在")
        return event

    def _event_for_write(self, event_id: int) -> PlatformRawEvent:
        return self._event(event_id, for_update=True)

    def _import_record(self, record_id: int, *, for_update: bool = False) -> DataImportRecord:
        statement = select(DataImportRecord).where(
            DataImportRecord.id == record_id,
            DataImportRecord.organization_id == self.principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        record = self.session.scalar(statement)
        if record is None:
            raise IngestionNotFoundError("导入记录不存在")
        return record

    def _import_event(
        self, record: DataImportRecord, event_id: int, *, for_update: bool = False
    ) -> PlatformRawEvent:
        if record.raw_event_id != event_id:
            raise AuthorizationError("原始事件不属于该导入记录")
        statement = select(PlatformRawEvent).where(
            PlatformRawEvent.id == event_id,
            PlatformRawEvent.organization_id == self.principal.organization_id,
            PlatformRawEvent.shop_id == record.shop_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        event = self.session.scalar(statement)
        if event is None:
            raise IngestionNotFoundError("导入原始事件不存在")
        return event

    def _event_for_processing(
        self,
        event_id: int,
        *,
        sync_job_id: int | None,
        sync_job_claim_token: str | None,
        require_observation: bool = True,
    ) -> PlatformRawEvent:
        snapshot = self._event(event_id)
        if sync_job_id is None or sync_job_claim_token is None:
            if sync_job_id is not None or sync_job_claim_token is not None:
                raise IngestionValidationError("原始事件处理必须同时提供同步任务和任务租约")
            observation_count = self.session.scalar(
                select(func.count())
                .select_from(SyncJobRawEvent)
                .where(SyncJobRawEvent.raw_event_id == snapshot.id)
            )
            if observation_count or not get_settings().allows_fixtures:
                raise IngestionValidationError("原始事件处理必须绑定可信同步任务")
            return self._event_for_write(event_id)

        job, _ = self._locked_ready_job(sync_job_id, expected_shop_id=snapshot.shop_id)
        self._require_job_claim(job, sync_job_claim_token)
        event = self._event_for_write(event_id)
        if event.shop_id != job.shop_id:
            raise AuthorizationError("同步任务不属于原始事件店铺")
        if require_observation:
            observation = self.session.scalar(
                select(SyncJobRawEvent).where(
                    SyncJobRawEvent.sync_job_id == job.id,
                    SyncJobRawEvent.raw_event_id == event.id,
                )
            )
            if observation is None:
                raise IngestionTransitionError("同步任务未观察到该原始事件")
        return event

    @staticmethod
    def _claim_matches(stored_hash: str | None, claim_token: str) -> bool:
        if stored_hash is None:
            return False
        try:
            candidate = _claim_hash(claim_token)
        except IngestionValidationError:
            return False
        return secrets.compare_digest(stored_hash, candidate)

    @staticmethod
    def _require_unexpired(expires_at: datetime | None, *, label: str) -> None:
        if expires_at is None or expires_at <= utcnow():
            raise IngestionTransitionError(f"{label}处理租约已过期")

    def _require_job_claim(
        self, job: SyncJob, claim_token: str, *, allow_terminal: bool = False
    ) -> None:
        if not self._claim_matches(job.lease_token_hash, claim_token):
            raise AuthorizationError("同步任务处理租约无效")
        if allow_terminal and job.status in {
            SyncJobStatus.SUCCESS,
            SyncJobStatus.PARTIAL,
            SyncJobStatus.FAILED,
        }:
            return
        if job.status is not SyncJobStatus.RUNNING:
            raise IngestionTransitionError("同步任务未处于运行状态")
        self._require_unexpired(job.lease_expires_at, label="同步任务")

    def _require_event_claim(
        self, event: PlatformRawEvent, claim_token: str, *, allow_terminal: bool = False
    ) -> None:
        if not self._claim_matches(event.processing_token_hash, claim_token):
            raise AuthorizationError("原始事件处理租约无效")
        if allow_terminal and event.status in {RawEventStatus.PROCESSED, RawEventStatus.FAILED}:
            return
        if event.status is not RawEventStatus.PROCESSING:
            raise IngestionTransitionError("原始事件未处于处理状态")
        self._require_unexpired(event.processing_lease_expires_at, label="原始事件")

    def _observe_event(
        self, job: SyncJob, event: PlatformRawEvent, *, requires_processing: bool = False
    ) -> None:
        existing = self.session.scalar(
            select(SyncJobRawEvent)
            .where(
                SyncJobRawEvent.sync_job_id == job.id,
                SyncJobRawEvent.raw_event_id == event.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = utcnow()
        if existing is not None:
            existing.last_observed_at = now
            existing.observation_count += 1
            if requires_processing:
                existing.processed_at = None
            return
        self.session.add(
            SyncJobRawEvent(
                sync_job_id=job.id,
                raw_event_id=event.id,
                organization_id=job.organization_id,
                shop_id=job.shop_id,
                first_observed_at=now,
                last_observed_at=now,
                processed_at=(
                    None
                    if requires_processing or event.status is not RawEventStatus.PROCESSED
                    else event.processed_at
                ),
            )
        )
        self.session.flush()

    @staticmethod
    def _verify_event_evidence(event: PlatformRawEvent) -> None:
        _, payload_hash = _canonical_json(
            event.payload, max_bytes=MAX_RAW_PAYLOAD_BYTES, label="原始事件 payload"
        )
        source_key = hashlib.sha256(
            f"{event.event_type}\0{event.external_event_id}".encode()
        ).hexdigest()
        if payload_hash != event.payload_hash or source_key != event.source_event_key:
            raise IngestionConflictError("原始事件证据完整性校验失败")

    def _audit(self, tool_name: str, details: dict[str, object]) -> None:
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name=tool_name,
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    **details,
                },
                tool_output={"status": "SUCCESS"},
                duration_ms=0,
                status="SUCCESS",
            )
        )
