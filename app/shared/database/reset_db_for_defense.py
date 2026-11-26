# -*- coding: utf-8 -*-
"""
reset_db_for_defense.py
───────────────────────
Script đưa cơ sở dữ liệu DroneOptAI (25 bảng chuẩn) về trạng thái SẠCH & TỐT NHẤT
để sẵn sàng demo trước hội đồng bảo vệ đồ án.

Nguyên tắc thiết kế:
  1. GIỮ NGUYÊN 100% hạ tầng & danh mục chuẩn (Master Data):
     hubs, accounts, battery_model_catalog, part_categories, hub_supplies, maintenance_schedules.
  2. GIỮ NGUYÊN toàn bộ đội bay (drones) & pin (batteries) đã chuẩn hóa mã, nhưng RESET
     trạng thái về 100% sẵn sàng bay (Available / Active, pin 100%, tháo pin khỏi drone/WO,
     giờ bay = 0, không có drone/pin mồ côi khỏi Hub).
  3. XÓA SẠCH toàn bộ dữ liệu nghiệp vụ phát sinh & nhật ký (17 bảng transactional/logs)
     theo đúng thứ tự khóa ngoại (FK), đồng thời reset các SERIAL/IDENTITY sequence về 1.
  4. AN TOÀN TUYỆT ĐỐI:
     - Đọc DATABASE_URL từ .env (không hardcode mật khẩu).
     - Mặc định chạy chế độ DRY-RUN (chỉ đọc, không sửa DB). Chỉ thực thi khi truyền `--execute`.
     - Toàn bộ thao tác chạy trong 1 PostgreSQL Transaction duy nhất (`engine.begin()`):
       gặp bất kỳ lỗi nào sẽ tự động ROLLBACK 100%.
     - Tự động chụp bản sao lưu JSON trước khi xóa (trừ khi truyền `--no-backup`).

Cách sử dụng:
  1. Xem trước tác động (Dry-run, an toàn 100% - không sửa DB):
       python -u app/shared/database/reset_db_for_defense.py

  2. Thực thi Reset sạch DB (có hỏi xác nhận + tự động backup JSON):
       python -u app/shared/database/reset_db_for_defense.py --execute

  3. Thực thi không hỏi lại (phù hợp chạy tự động):
       python -u app/shared/database/reset_db_for_defense.py --execute --yes
       python -u app/shared/database/reset_db_for_defense.py --execute --yes --no-backup
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

# Bảo vệ chống treo socket/DNS trên Windows (tuân thủ GEMINI.md)
_WATCHDOG_SEC = float(os.getenv("RESET_DB_TIMEOUT_SEC", "60"))
_wd = threading.Timer(
    _WATCHDOG_SEC,
    lambda: (print("\n[WATCHDOG_TIMEOUT] Quá thời gian chờ kết nối DB, tự thoát an toàn.", flush=True), os._exit(2)),
)
_wd.daemon = True
_wd.start()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo import được `app.*` dù chạy từ bất kỳ thư mục nào
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from sqlalchemy import text
from app.core.timezone import get_vn_naive_datetime
from app.database.db import engine


# ── Phân loại 25 bảng chính thức của hệ thống ──────────────────────────────────

# Nhóm A: Master Data (giữ nguyên cấu trúc & dữ liệu nền)
MASTER_TABLES = [
    "hubs",
    "accounts",
    "battery_model_catalog",
    "part_categories",
    "hub_supplies",
    "maintenance_schedules",
]

# Nhóm B: Đội bay & Pin (KHÔNG BAO GIỜ XÓA — giữ nguyên toàn bộ thiết bị tại tất cả các Hub,
# bao gồm Da Kao Hub (20) & Hiep Binh Hub (21), chỉ reset trạng thái về 100% sẵn sàng)
FLEET_RESET_TABLES = [
    "drones",
    "batteries",
]

# Nhóm C: Dữ liệu nghiệp vụ & nhật ký (XÓA SẠCH theo đúng thứ tự Con -> Cha để thỏa FK)
CLEAR_ORDER = [
    "delivery_ratings",       # FK -> orders, accounts
    "telemetry_logs",         # FK -> missions, drones
    "ai_predictions",         # ref -> missions
    "mission_legs",           # FK -> missions, batteries, hubs
    "replacement_requests",   # FK -> work_orders, drones, batteries, part_categories, hubs
    "parts_requests",         # FK -> work_orders, part_categories, hubs
    "supply_transactions",    # FK -> work_orders, part_categories, hubs
    "maintenance_alerts",     # FK -> work_orders, drones, batteries (đã gỡ vòng lặp FK trước)
    "incidents",              # FK -> missions, drones
    "work_orders",            # FK -> drones, batteries, maintenance_alerts (đã gỡ vòng lặp FK trước)
    "missions",               # FK -> orders, accounts, drones, batteries
    "orders",                 # FK -> accounts, hubs
    "drone_daily_stats",      # FK -> drones
    "saved_addresses",        # FK -> accounts, hubs
    "notifications",          # ref -> accounts, drones, work_orders, missions
    "activity_logs",          # ref -> accounts
    "system_logs",            # system cron logs
]

ALL_25_TABLES = MASTER_TABLES + FLEET_RESET_TABLES + CLEAR_ORDER

# Các bảng có khóa chính BIGSERIAL/SERIAL cần reset sequence về 1 sau khi DELETE
SERIAL_ID_TABLES = [
    "delivery_ratings",
    "telemetry_logs",
    "ai_predictions",
    "mission_legs",
    "replacement_requests",
    "parts_requests",
    "supply_transactions",
    "maintenance_alerts",
    "incidents",
    "work_orders",
    "drone_daily_stats",
    "saved_addresses",
    "notifications",
    "activity_logs",
    "system_logs",
]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def _fetch_counts(conn, tables: list[str]) -> dict[str, int]:
    """Đếm số dòng hiện có của tất cả bảng tồn tại trong schema public."""
    existing = {
        r[0]
        for r in conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchall()
    }
    counts: dict[str, int] = {}
    present = [t for t in tables if t in existing]
    if not present:
        return counts
    union_sql = " UNION ALL ".join(
        f"SELECT '{t}' AS tbl, COUNT(*)::bigint AS cnt FROM \"{t}\"" for t in present
    )
    for tbl, cnt in conn.execute(text(union_sql)).fetchall():
        counts[tbl] = int(cnt)
    return counts


def _backup_before_reset(conn, tables_to_backup: list[str], output_path: Path) -> None:
    """Chụp nhanh snapshot JSON của các bảng bị sửa/xóa trước khi reset để có thể khôi phục."""
    existing = {
        r[0]
        for r in conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).fetchall()
    }
    payload: dict[str, Any] = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": "Auto backup generated by reset_db_for_defense.py before clearing database",
        "tables": {},
    }
    for tbl in tables_to_backup:
        if tbl not in existing:
            continue
        res = conn.execute(text(f'SELECT * FROM "{tbl}"'))
        cols = list(res.keys())
        rows = [list(r) for r in res.fetchall()]
        payload["tables"][tbl] = {"columns": cols, "row_count": len(rows), "rows": rows}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=_json_default)


def _print_summary_table(before: dict[str, int], after: dict[str, int] | None, clear_tables: list[str]) -> None:
    print("\n" + "=" * 86, flush=True)
    header = f"{'Bảng':<26} | {'Nhóm':<16} | {'Trước':>8} | {'Sau' if after else 'Dự kiến sau':>12}"
    print(header, flush=True)
    print("-" * 86, flush=True)

    for tbl in ALL_25_TABLES:
        if tbl not in before:
            continue
        if tbl in MASTER_TABLES:
            grp = "GIỮ (Master)"
            expected = str(before[tbl])
        elif tbl in FLEET_RESET_TABLES:
            grp = "RESET trạng thái"
            expected = str(before[tbl])
        elif tbl in clear_tables:
            grp = "CLEAR sạch"
            expected = "0"
        else:
            grp = "GIỮ (Tùy chọn)"
            expected = str(before[tbl])

        after_val = str(after.get(tbl, "-")) if after is not None else expected
        print(f"{tbl:<26} | {grp:<16} | {before[tbl]:>8} | {after_val:>12}", flush=True)
    print("=" * 86, flush=True)


def run_reset(
    execute: bool = False,
    yes: bool = False,
    backup: bool = True,
    keep_saved_addresses: bool = False,
    reactivate_all_accounts: bool = False,
    default_hub_id: int = 1,
) -> None:
    t0 = time.perf_counter()
    clear_tables = [t for t in CLEAR_ORDER if not (keep_saved_addresses and t == "saved_addresses")]

    print(f"[{time.perf_counter()-t0:.2f}s] Kết nối Database (chế độ: {'EXECUTE' if execute else 'DRY-RUN'})...", flush=True)
    with engine.connect() as conn:
        before_counts = _fetch_counts(conn, ALL_25_TABLES)
        hub_exists = conn.execute(
            text("SELECT id FROM hubs WHERE id = :hid"), {"hid": default_hub_id}
        ).scalar()
        if hub_exists is None:
            first_hub = conn.execute(text("SELECT id FROM hubs ORDER BY id ASC LIMIT 1")).scalar()
            if first_hub is None:
                raise RuntimeError("Bảng hubs đang trống — dừng lại để tránh làm hỏng dữ liệu đội bay!")
            default_hub_id = int(first_hub)

    _print_summary_table(before_counts, after=None, clear_tables=clear_tables)

    if not execute:
        print(
            "\n[DRY-RUN] Không có dữ liệu nào bị thay đổi."
            "\nĐể thực thi reset sạch DB, chạy lại với cờ `--execute`:\n"
            "  python -u app/shared/database/reset_db_for_defense.py --execute",
            flush=True,
        )
        return

    if not yes:
        confirm = input(
            "\nCẢNH BÁO: Bạn sắp XÓA SẠCH toàn bộ dữ liệu nghiệp vụ (orders, missions, logs, ...) "
            "và RESET đội bay/pin về 100% sẵn sàng.\nNhập 'RESET' để xác nhận: "
        ).strip()
        if confirm != "RESET":
            print("Đã hủy thao tác. Database giữ nguyên.", flush=True)
            return

    if backup:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = SCRIPT_DIR / f"backup_before_defense_reset_{ts}.json"
        print(f"\n[{time.perf_counter()-t0:.2f}s] Đang chụp backup JSON trước khi xóa -> {backup_path.name}...", flush=True)
        # Bỏ system_logs khi backup nếu quá lớn, giữ toàn bộ bảng nghiệp vụ & fleet
        backup_tables = FLEET_RESET_TABLES + ["hub_supplies", "accounts"] + [t for t in clear_tables if t != "system_logs"]
        with engine.connect() as conn:
            _backup_before_reset(conn, backup_tables, backup_path)
        print(f"[{time.perf_counter()-t0:.2f}s] Backup hoàn tất ({backup_path.stat().st_size / 1024:.1f} KB).", flush=True)

    now_vn = get_vn_naive_datetime()
    print(f"\n[{time.perf_counter()-t0:.2f}s] Bắt đầu Transaction Reset toàn bộ Database...", flush=True)

    with engine.begin() as conn:
        # 1. Gỡ các vòng lặp FK & liên kết từ batteries -> work_orders/drones
        conn.execute(text("UPDATE work_orders SET alert_id = NULL WHERE alert_id IS NOT NULL"))
        conn.execute(text("UPDATE maintenance_alerts SET work_order_id = NULL WHERE work_order_id IS NOT NULL"))
        conn.execute(text("UPDATE batteries SET drone_id = NULL, work_order_id = NULL"))

        # 2. Xóa sạch 17 bảng nghiệp vụ & nhật ký theo đúng thứ tự FK (Con -> Cha)
        existing = {
            r[0]
            for r in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            ).fetchall()
        }
        for tbl in clear_tables:
            if tbl not in existing:
                continue
            res = conn.execute(text(f'DELETE FROM "{tbl}"'))
            print(f"  - DELETE {tbl:<24}: {res.rowcount:>6} dòng", flush=True)

        # 3. Reset SERIAL/BIGSERIAL sequences về 1 cho các bảng vừa xóa sạch
        for tbl in SERIAL_ID_TABLES:
            if tbl not in clear_tables or tbl not in existing:
                continue
            seq_name = conn.execute(
                text("SELECT pg_get_serial_sequence(:tbl, 'id')"), {"tbl": f"public.{tbl}"}
            ).scalar()
            if seq_name:
                conn.execute(text("SELECT setval(:seq, 1, false)"), {"seq": seq_name})

        # 4. Reset toàn bộ đội bay (drones) về trạng thái 100% sẵn sàng tại Hub
        res_drones = conn.execute(
            text("""
                UPDATE drones
                SET operational_status = 'Available',
                    battery_level_pct  = 100,
                    utilization_pct    = 0.0,
                    total_flight_hours = 0.0,
                    last_maintenance_at = NULL,
                    current_hub_id     = COALESCE(current_hub_id, :default_hub_id)
            """),
            {"default_hub_id": default_hub_id},
        )
        print(f"  - RESET  {'drones':<24}: {res_drones.rowcount:>6} drone -> Available (100% pin, 0h bay)", flush=True)

        # 5. Reset toàn bộ kho pin (batteries) về trạng thái Active, 100% đầy pin tại Hub
        res_bats = conn.execute(
            text("""
                UPDATE batteries
                SET status           = 'Active',
                    charge_level_pct = 100,
                    drone_id         = NULL,
                    work_order_id    = NULL,
                    last_charged_at  = :now_vn,
                    current_hub_id   = COALESCE(current_hub_id, :default_hub_id)
            """),
            {"now_vn": now_vn, "default_hub_id": default_hub_id},
        )
        print(f"  - RESET  {'batteries':<24}: {res_bats.rowcount:>6} pin   -> Active (100% pin, đã tháo rời)", flush=True)

        # 6. Nạp lại tồn kho vật tư hub_supplies bị thấp về mức an toàn (tối thiểu max(min_stock_threshold + 10, 20))
        res_sup = conn.execute(
            text("""
                UPDATE hub_supplies
                SET quantity   = GREATEST(quantity, min_stock_threshold + 10, 20),
                    updated_at = NOW()
                WHERE quantity < GREATEST(min_stock_threshold + 10, 20)
            """)
        )
        print(f"  - REFILL {'hub_supplies':<24}: {res_sup.rowcount:>6} dòng vật tư được bổ sung mức tồn kho khỏe", flush=True)

        # 7. Dọn token reset mật khẩu cũ trong accounts (giữ nguyên toàn bộ user & mật khẩu)
        conn.execute(
            text("""
                UPDATE accounts
                SET reset_token = NULL,
                    reset_token_expires_at = NULL
                WHERE reset_token IS NOT NULL OR reset_token_expires_at IS NOT NULL
            """)
        )
        if reactivate_all_accounts:
            conn.execute(text("UPDATE accounts SET is_active = true WHERE email != 'test@test.com'"))

    # 8. Post-Reset Health Check (Read-Only)
    print(f"\n[{time.perf_counter()-t0:.2f}s] Đang chạy Post-Reset Health Check...", flush=True)
    with engine.connect() as conn:
        after_counts = _fetch_counts(conn, ALL_25_TABLES)
        bad_drones = conn.execute(
            text("SELECT COUNT(*) FROM drones WHERE operational_status != 'Available' OR current_hub_id IS NULL")
        ).scalar()
        bad_bats = conn.execute(
            text("SELECT COUNT(*) FROM batteries WHERE status != 'Active' OR charge_level_pct != 100 OR drone_id IS NOT NULL OR current_hub_id IS NULL")
        ).scalar()
        low_sup = conn.execute(
            text("SELECT COUNT(*) FROM hub_supplies WHERE quantity < min_stock_threshold")
        ).scalar()

    _print_summary_table(before_counts, after=after_counts, clear_tables=clear_tables)
    print(
        f"\n[HEALTH CHECK] bad_drones={bad_drones} | bad_batteries={bad_bats} | low_supplies={low_sup}",
        flush=True,
    )
    if bad_drones or bad_bats or low_sup:
        raise RuntimeError("Post-Reset Health Check phát hiện trạng thái chưa chuẩn!")

    print(
        f"\n[{time.perf_counter()-t0:.2f}s] HOÀN TẤT RESET DATABASE SẴN SÀNG BẢO VỆ ĐỒ ÁN!"
        "\nLưu ý: Nếu backend server đang bật cache RAM (hubs/routes), hãy restart uvicorn sau khi reset.",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear & reset DroneOptAI database về trạng thái sạch chuẩn bảo vệ đồ án."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Thực thi thay đổi lên Database (mặc định không truyền là DRY-RUN chỉ đọc).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bỏ qua bước nhập 'RESET' xác nhận thủ công.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Không xuất file JSON backup trước khi xóa.",
    )
    parser.add_argument(
        "--keep-saved-addresses",
        action="store_true",
        help="Giữ lại danh bạ địa chỉ đã lưu của Customer (saved_addresses).",
    )
    parser.add_argument(
        "--reactivate-accounts",
        action="store_true",
        help="Bật lại is_active=true cho toàn bộ tài khoản (trừ test@test.com).",
    )
    args = parser.parse_args()

    try:
        run_reset(
            execute=args.execute,
            yes=args.yes,
            backup=not args.no_backup,
            keep_saved_addresses=args.keep_saved_addresses,
            reactivate_all_accounts=args.reactivate_accounts,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
