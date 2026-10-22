"""
migrate_battery_lifecycle.py
────────────────────────────────────────
Migrate DB cho 2 tính năng:

1. Battery lifecycle:
   - Thêm cột `last_charged_at TIMESTAMP` vào bảng `batteries`
     (ghi thời điểm pin được sạc đầy 100% gần nhất — bởi charging worker
     hoặc manual mark-charged endpoint).
   - Vòng đời pin (Active/Expired, days_until_expiry) được tính on-the-fly
     trong API response dựa trên `create_at`, không cần thêm cột.

2. Hub Supplies (quản lý vật tư tại Hub):
   - Tạo bảng `hub_supplies` mới theo dõi số lượng vật tư (cánh quạt, dụng cụ sửa chữa, ...)
     tại từng Hub. Không định danh từng đơn vị vật tư.

Thiết kế idempotent: dùng IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
→ an toàn khi chạy nhiều lần.

Chạy 1 lần sau khi pull code mới:
    python -m app.shared.database.migrate_battery_lifecycle
"""
import asyncio
from sqlalchemy import text
from app.database.db import AsyncSessionLocal


async def migrate():
    async with AsyncSessionLocal() as db:
        # ────────────────────────────────────────────────────────────────────
        # 1. Thêm cột last_charged_at vào bảng batteries
        # ────────────────────────────────────────────────────────────────────
        print("1. batteries — thêm cột last_charged_at …")
        await db.execute(text(
            "ALTER TABLE batteries "
            "ADD COLUMN IF NOT EXISTS last_charged_at TIMESTAMP;"
        ))
        print("   ✅ batteries.last_charged_at OK")

        # ────────────────────────────────────────────────────────────────────
        # 2. Tạo bảng hub_supplies
        # ────────────────────────────────────────────────────────────────────
        print("2. hub_supplies — tạo bảng quản lý vật tư tại Hub …")
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS hub_supplies (
                id           SERIAL PRIMARY KEY,
                hub_id       INT REFERENCES hubs(id) ON DELETE SET NULL,
                item_name    VARCHAR(200) NOT NULL,
                quantity     INT          NOT NULL DEFAULT 0,
                unit         VARCHAR(50),
                created_at   TIMESTAMP,
                updated_at   TIMESTAMP
            );
        """))
        # Index để truy vấn vật tư theo hub nhanh
        await db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hub_supplies_hub_id "
            "ON hub_supplies (hub_id);"
        ))
        print("   ✅ hub_supplies OK")

        await db.commit()
        print("\n✅  migrate_battery_lifecycle hoàn thành!")


if __name__ == "__main__":
    asyncio.run(migrate())
