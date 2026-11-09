# -*- coding: utf-8 -*-
"""
Script khôi phục (Rollback) toàn bộ dữ liệu Database từ bản sao lưu
`backup_before_clean_reset.json` (được tạo trước khi chạy Clean & Reset Database).

Cách sử dụng khi cần Rollback:
    python app/shared/database/rollback_clean_reset.py
"""

import json
import os
import sys
import psycopg2
from psycopg2.extras import execute_values

sys.stdout.reconfigure(encoding="utf-8")

BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup_before_clean_reset.json")
DB_URL = "postgresql://postgres.iavsnlhixjsxhuhpjyni:TanMy%40huntrot@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres"

# Thứ tự xóa trước khi nạp lại và thứ tự nạp lại để tôn trọng Foreign Keys
RESTORE_ORDER = [
    "hubs",
    "accounts",
    "battery_model_catalog",
    "part_categories",
    "drones",
    "batteries",
    "hub_supplies",
    "maintenance_schedules",
    "orders",
    "missions",
    "mission_legs",
    "ai_predictions",
    "telemetry_logs",
    "incidents",
    "maintenance_alerts",
    "work_orders",
    "parts_requests",
    "supply_transactions",
    "notifications",
    "activity_logs",
    "system_logs",
    "audit_logs",
]


def rollback_database():
    if not os.path.exists(BACKUP_FILE):
        print(f"[LỖI] Không tìm thấy file backup tại: {BACKUP_FILE}")
        sys.exit(1)

    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
        backup_data = json.load(f)

    print(f"Đang khôi phục dữ liệu từ bản backup ({backup_data.get('created_at')})...")
    tables_data = backup_data.get("tables", {})

    conn = psycopg2.connect(DB_URL)
    try:
        with conn:
            with conn.cursor() as cur:
                # Tạm thời vô hiệu hóa kiểm tra trigger/FK trong phiên giao dịch
                cur.execute("SET session_replication_role = 'replica';")

                # Xóa dữ liệu hiện tại theo thứ tự ngược
                for table in reversed(RESTORE_ORDER):
                    if table in tables_data:
                        cur.execute(f'DELETE FROM "{table}";')

                # Khôi phục dữ liệu từng bảng
                for table in RESTORE_ORDER:
                    if table not in tables_data:
                        continue
                    tinfo = tables_data[table]
                    cols = tinfo.get("columns", [])
                    rows = tinfo.get("rows", [])
                    if not cols or not rows:
                        print(f"  - {table:<25}: 0 dòng")
                        continue

                    col_names = ", ".join(f'"{c}"' for c in cols)
                    converted_rows = []
                    for r in rows:
                        converted_row = []
                        for val in r:
                            if isinstance(val, (dict, list)):
                                converted_row.append(json.dumps(val, ensure_ascii=False))
                            else:
                                converted_row.append(val)
                        converted_rows.append(tuple(converted_row))

                    insert_sql = f'INSERT INTO "{table}" ({col_names}) VALUES %s'
                    execute_values(cur, insert_sql, converted_rows)
                    print(f"  - {table:<25}: Đã khôi phục {len(rows)} dòng")

                cur.execute("SET session_replication_role = 'origin';")
        print("\n✅ KHÔI PHỤC (ROLLBACK) DATABASE THÀNH CÔNG 100%!")
    finally:
        conn.close()


if __name__ == "__main__":
    rollback_database()
