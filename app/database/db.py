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
    pool_recycle=3600,
    connect_args={"sslmode": "require"}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine — dùng cho Auth và các module async

# --- CODE CŨ (Dành cho kết nối trực tiếp - Direct Connection IPv6 - Port 5432) ---
# async_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

# --- CODE MỚI (Dành cho kết nối qua Pooler IPv4 - Transaction Mode - Port 6543) ---
# Khi đi qua Pooler ở chế độ Transaction, ta phải tắt cache statement của asyncpg
# nếu không sẽ gặp lỗi "prepared statement does not exist"
async_engine = create_async_engine(
    DATABASE_URL, 
    pool_pre_ping=True,
    connect_args={
        "statement_cache_size": 0  # Tắt cache prepared statement của asyncpg để không bị lỗi với Transaction Pooler
    }
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session