"""
FDE-192 + FDE-194: Isolated DB engine for cronjobs + Auto-Retry mechanism

Tại sao cần engine riêng cho cron?
- Cronjob dùng chung pool với FastAPI → tranh connection → API latency tăng khi cron chạy nặng.
- Pool riêng giới hạn 2 connections (đủ cho cron) + max_overflow=1, không ảnh hưởng API pool.

Tại sao mỗi worker cần session riêng?
- Nếu worker A lỗi giữa chừng mà session chưa rollback, worker B dùng chung session đó
  sẽ thấy dirty state → lỗi lan dây chuyền.
- Mỗi worker tự open/close session của mình → cô lập hoàn toàn.
"""

import asyncio
import logging
import os
from typing import Callable

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("cron")

DATABASE_URL = os.getenv("DATABASE_URL")

# ── FDE-192: Engine độc lập cho cronjob ───────────────────────────────────────
# pool_size=2  → tối đa 2 kết nối thường trực (đủ cho cron không tranh với API)
# max_overflow=1 → cho phép thêm 1 kết nối tạm khi burst
# pool_timeout=10 → nếu không lấy được conn trong 10s thì raise ngay (không treo)
# pool_recycle=1800 → tái tạo conn sau 30 phút để tránh conn bị cloud provider cắt
cron_async_engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=1,
    pool_timeout=10,
    pool_recycle=1800,
    connect_args={
        "statement_cache_size": 0  # Bắt buộc với Transaction Pooler (Supabase/PgBouncer)
    },
)

CronSessionLocal = async_sessionmaker(cron_async_engine, expire_on_commit=False)


# ── FDE-194: Auto-Retry với Exponential Backoff ───────────────────────────────

# Các exception được coi là "lỗi tạm thời" → đáng retry
_TRANSIENT_ERRORS = (
    OSError,             # Network-level: connection reset, broken pipe
    TimeoutError,        # Connection/query timeout
    ConnectionError,     # General connection failure
)

# Thêm SQLAlchemy async exceptions nếu cần
try:
    from sqlalchemy.exc import OperationalError, DisconnectionError
    _TRANSIENT_ERRORS = _TRANSIENT_ERRORS + (OperationalError, DisconnectionError)
except ImportError:
    pass


def _is_transient(exc: Exception) -> bool:
    """Trả về True nếu exception này đáng được retry."""
    if isinstance(exc, _TRANSIENT_ERRORS):
        return True
    # asyncpg connection errors thường có message chứa các keyword này
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "connection", "timeout", "closed", "reset", "broken pipe",
        "ssl connection", "server closed",
    ))


async def run_with_retry(
    worker_fn: Callable[[AsyncSession], None],
    worker_name: str,
    max_retries: int = 3,
    base_backoff: float = 2.0,
) -> None:
    """
    FDE-194: Chạy một worker function với cơ chế tự động thử lại.

    Quy trình mỗi lần thử:
      1. Mở session MỚI từ CronSessionLocal (FDE-193: session độc lập).
      2. Gọi worker_fn(session).
      3. Nếu thành công → return.
      4. Nếu lỗi tạm thời (connection/timeout) → rollback → đợi backoff → retry.
      5. Nếu lỗi logic (không phải transient) → rollback → raise ngay, không retry.
      6. Sau khi hết max_retries → log [CRON FAILED] và bỏ qua (không crash toàn server).

    Backoff schedule (base=2s): 2s → 4s → 8s
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            async with CronSessionLocal() as session:
                await worker_fn(session)
            return  # thành công

        except Exception as exc:
            last_exc = exc
            if _is_transient(exc):
                wait = base_backoff ** attempt
                logger.warning(
                    "[CRON RETRY] %s | attempt %d/%d | lỗi tạm thời: %s | "
                    "thử lại sau %.1fs",
                    worker_name, attempt, max_retries, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                # Lỗi logic → không retry, log và thoát
                logger.error(
                    "[CRON LOGIC ERROR] %s | attempt %d | lỗi không thể retry: %s",
                    worker_name, attempt, exc, exc_info=True,
                )
                return

    logger.error(
        "[CRON FAILED] %s | đã thử %d lần, bỏ qua. Lỗi cuối: %s",
        worker_name, max_retries, last_exc, exc_info=True,
    )
