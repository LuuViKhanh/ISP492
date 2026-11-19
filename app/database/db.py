import os

from sqlalchemy import create_engine
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
    pool_recycle=180,
    connect_args={"sslmode": "require"}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine — dùng cho Auth và các module async

# --- CẤU HÌNH TỐI ƯU CHO SUPABASE TRANSACTION POOLER (IPv4 - Port 6543) ---
# 1. pool_size=20: Giữ 20 kết nối thường trực, phục vụ trọn vẹn 1 page-load burst (7-10 APIs song song)
# 2. max_overflow=20: Cho phép mở thêm kết nối tức thời khi có đột biến truy cập
# 3. pool_timeout=20: Giới hạn thời gian chờ hàng đợi trong pool (20s) tránh proxy/Cloudflare bị treo 502
# 4. pool_recycle=180: Tái tạo kết nối mỗi 3 phút để tránh Supabase ngắt ngầm các socket idle (nguyên nhân gây 500)
# 5. pool_pre_ping=True: Tự động ping kiểm tra kết nối sống trước khi checkout
# 6. statement_cache_size=0: Bắt buộc với Transaction Pooler (PgBouncer/Supavisor)
async_engine = create_async_engine(
    DATABASE_URL, 
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=20,
    pool_timeout=20,
    pool_recycle=180,
    connect_args={
        "statement_cache_size": 0,  # Tắt cache prepared statement của asyncpg để tương thích Transaction Pooler
        "command_timeout": 30,      # Timeout cho mỗi query (30s), tránh request treo vô hạn
        "timeout": 15,              # Timeout kết nối socket ban đầu tới Supabase (15s)
        "server_settings": {
            "application_name": "drone_backend_api"
        }
    }
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session
