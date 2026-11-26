import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")  # asyncpg URL

# Sync engine — dùng cho Dev DB router (psycopg2)
SYNC_DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg", "postgresql")

engine = create_engine(
    SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=900,
    connect_args={"sslmode": "require"}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine — dùng cho Auth và các module async

# --- CẤU HÌNH TỐI ƯU CHO SUPABASE TRANSACTION POOLER (IPv4 - Port 6543) ---
# 1. pool_size=10, max_overflow=15: Đủ cho burst page-load mà vẫn giữ kết nối tập trung để tái sử dụng Enum codecs đã warm.
# 2. pool_timeout=20: Giới hạn thời gian chờ hàng đợi trong pool (20s) tránh proxy/Cloudflare bị treo 502
# 3. pool_recycle=900: Giữ kết nối warm 15 phút (có pool_pre_ping=True tự bắt kết nối chết) để tránh mất Enum cache sau mỗi 3 phút
# 4. pool_pre_ping=True: Tự động ping kiểm tra kết nối sống trước khi checkout
# 5. statement_cache_size=0: Bắt buộc với Transaction Pooler (PgBouncer/Supavisor)
async_engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=15,
    pool_timeout=20,
    pool_recycle=900,
    connect_args={
        "statement_cache_size": 0,  # Tắt cache prepared statement của asyncpg để tương thích Transaction Pooler
        "command_timeout": 30,      # Timeout cho mỗi query (30s), tránh request treo vô hạn
        "timeout": 15,              # Timeout kết nối socket ban đầu tới Supabase (15s)
        "server_settings": {
            "application_name": "drone_backend_api"
        }
    }
)

_ENUM_WARMUP_SQL = (
    "SELECT "
    "NULL::orders_status_enum, "
    "NULL::payment_status_enum, "
    "NULL::delivery_mode_enum, "
    "NULL::refund_status_enum, "
    "NULL::drone_status, "
    "NULL::batteries_status, "
    "NULL::missions_status, "
    "NULL::missions_handling_status, "
    "NULL::mission_leg_status_enum, "
    "NULL::notifications_type, "
    "NULL::incidents_status, "
    "NULL::risk_level, "
    "NULL::work_orders_status, "
    "NULL::part_category_type_enum, "
    "NULL::parts_request_status_enum, "
    "NULL::replacement_status_enum, "
    "NULL::replacement_type_enum, "
    "NULL::supply_txn_reason_enum"
)


@event.listens_for(async_engine.sync_engine, "connect")
def _warm_asyncpg_enum_codecs(dbapi_connection, connection_record):
    """
    [PERF NOTE] Nạp sẵn toàn bộ codec PostgreSQL ENUM cho connection mới ngay lúc mở
    trong 1 lượt duy nhất, tránh việc asyncpg dò pg_type lặp lại (~1.25s/bảng) trong API request.
    """
    try:
        driver_conn = getattr(dbapi_connection, "_connection", None) or getattr(
            dbapi_connection, "driver_connection", None
        )
        if driver_conn is not None and hasattr(dbapi_connection, "await_"):
            dbapi_connection.await_(driver_conn.fetchrow(_ENUM_WARMUP_SQL))
    except Exception as exc:
        print(f"[DB WARMUP WARN] Enum codec warmup failed: {exc}")


AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session
