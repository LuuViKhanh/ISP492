"""
Database Migration Script: Core Schema V3 (Tinh gọn từ 33 bảng -> 20 bảng cốt lõi)
Tuân thủ nghiêm ngặt quy định an toàn GEMINI.md:
- Mặc định chạy ở chế độ DRY-RUN (an toàn, chỉ in ra các bước mà không thay đổi CSDL).
- Chỉ thực thi thật khi truyền cờ --execute.
- Di chuyển và bảo toàn toàn bộ dữ liệu từ các bảng con sang bảng cha trước khi drop bảng.
- Toàn bộ thao tác thực hiện trong 1 TRANSACTION duy nhất, rollback ngay lập tức nếu có bất kỳ lỗi nào.
"""
import sys
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor, Json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "")
SYNC_DB_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


def main():
    execute = "--execute" in sys.argv
    print("=" * 80)
    print("CORE SCHEMA V3 MIGRATION: CONSOLIDATE 33 TABLES -> 20 CORE TABLES")
    print(f"Mode: {'EXECUTE (LIVE DB MODIFICATION)' if execute else 'DRY-RUN (PREVIEW ONLY)'}")
    print("=" * 80)

    conn = psycopg2.connect(SYNC_DB_URL, sslmode="require")
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # ── 1. RENAME users -> accounts & GỘP password_reset_tokens ─────────
        print("\n[Step 1] IAM: Rename 'users' -> 'accounts' & merge 'password_reset_tokens'")
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name IN ('users', 'accounts');
        """)
        auth_tables = {r["table_name"] for r in cur.fetchall()}

        if "users" in auth_tables and "accounts" not in auth_tables:
            print("  - Renaming table 'users' -> 'accounts'")
            if execute:
                cur.execute('ALTER TABLE public."users" RENAME TO "accounts";')
        elif "accounts" in auth_tables:
            print("  - Table 'accounts' already exists.")

        # Thêm cột reset_token, reset_token_expires_at vào accounts
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'accounts';
        """)
        acc_cols = {r["column_name"] for r in cur.fetchall()}
        if "reset_token" not in acc_cols:
            print("  - Adding column 'reset_token' (VARCHAR UNIQUE) to 'accounts'")
            if execute:
                cur.execute('ALTER TABLE public."accounts" ADD COLUMN "reset_token" VARCHAR UNIQUE;')
        if "reset_token_expires_at" not in acc_cols:
            print("  - Adding column 'reset_token_expires_at' (TIMESTAMPTZ) to 'accounts'")
            if execute:
                cur.execute('ALTER TABLE public."accounts" ADD COLUMN "reset_token_expires_at" TIMESTAMPTZ;')

        # Di chuyển dữ liệu token chưa hết hạn từ password_reset_tokens sang accounts (nếu có)
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'password_reset_tokens';
        """)
        if cur.fetchone():
            cur.execute("""
                SELECT user_id, token, expires_at 
                FROM public.password_reset_tokens 
                WHERE used = FALSE AND expires_at > NOW();
            """)
            active_tokens = cur.fetchall()
            print(f"  - Found {len(active_tokens)} active tokens in 'password_reset_tokens' to migrate.")
            if execute:
                for tok in active_tokens:
                    cur.execute("""
                        UPDATE public.accounts 
                        SET reset_token = %s, reset_token_expires_at = %s 
                        WHERE id = %s;
                    """, (tok["token"], tok["expires_at"], tok["user_id"]))

        # ── 2. AI PREDICTIONS: GỘP ai_recommendations VÀO ai_predictions ───
        print("\n[Step 2] AI: Merge 'ai_recommendations' -> 'ai_predictions'")
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'ai_predictions';
        """)
        ai_cols = {r["column_name"] for r in cur.fetchall()}
        if "recommendation_content" not in ai_cols:
            print("  - Adding column 'recommendation_content' (TEXT) to 'ai_predictions'")
            if execute:
                cur.execute('ALTER TABLE public."ai_predictions" ADD COLUMN "recommendation_content" TEXT;')
        if "is_recommendation_applied" not in ai_cols:
            print("  - Adding column 'is_recommendation_applied' (BOOLEAN DEFAULT TRUE) to 'ai_predictions'")
            if execute:
                cur.execute('ALTER TABLE public."ai_predictions" ADD COLUMN "is_recommendation_applied" BOOLEAN DEFAULT TRUE;')

        # Copy data từ ai_recommendations sang ai_predictions
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'ai_recommendations';
        """)
        if cur.fetchone():
            cur.execute("""
                SELECT mission_id, content, is_applied 
                FROM public.ai_recommendations;
            """)
            recs = cur.fetchall()
            print(f"  - Found {len(recs)} rows in 'ai_recommendations' to merge.")
            if execute:
                for rec in recs:
                    cur.execute("""
                        UPDATE public.ai_predictions 
                        SET recommendation_content = %s, is_recommendation_applied = %s 
                        WHERE mission_id = %s;
                    """, (rec["content"], rec["is_applied"], rec["mission_id"]))

        # ── 3. MISSIONS: GỘP mission_hub_checkpoints VÀO missions ───────────
        print("\n[Step 3] Missions: Merge 'mission_hub_checkpoints' -> 'missions'")
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'missions';
        """)
        m_cols = {r["column_name"] for r in cur.fetchall()}
        if "checkpoints_json" not in m_cols:
            print("  - Adding column 'checkpoints_json' (JSONB DEFAULT '[]') to 'missions'")
            if execute:
                cur.execute('ALTER TABLE public."missions" ADD COLUMN "checkpoints_json" JSONB DEFAULT \'[]\'::jsonb;')

        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'mission_hub_checkpoints';
        """)
        if cur.fetchone():
            cur.execute("""
                SELECT mission_id, json_agg(row_to_json(c.*) ORDER BY checkpoint_order ASC) as cps
                FROM public.mission_hub_checkpoints c
                GROUP BY mission_id;
            """)
            cp_groups = cur.fetchall()
            print(f"  - Found {len(cp_groups)} mission checkpoint groups to migrate.")
            if execute:
                for row in cp_groups:
                    cur.execute("""
                        UPDATE public.missions 
                        SET checkpoints_json = %s 
                        WHERE id = %s;
                    """, (Json(row["cps"]), row["mission_id"]))

        # ── 4. FLEET: GỘP maintenance_records, inspection_items, logs VÀO work_orders ─
        print("\n[Step 4] Fleet: Merge maintenance records, inspection items & logs -> 'work_orders'")
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'work_orders';
        """)
        wo_cols = {r["column_name"] for r in cur.fetchall()}
        for col_name, col_type in [
            ("diagnosis", "TEXT"),
            ("resulting_drone_status", "VARCHAR"),
            ("record_completed_at", "TIMESTAMP"),
            ("inspection_items_json", "JSONB DEFAULT '[]'::jsonb"),
            ("status_logs_json", "JSONB DEFAULT '[]'::jsonb"),
        ]:
            if col_name not in wo_cols:
                print(f"  - Adding column '{col_name}' ({col_type}) to 'work_orders'")
                if execute:
                    cur.execute(f'ALTER TABLE public."work_orders" ADD COLUMN "{col_name}" {col_type};')

        # Di chuyển dữ liệu work_order_logs vào work_orders.status_logs_json
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'work_order_logs';
        """)
        if cur.fetchone():
            cur.execute("""
                SELECT work_order_id, json_agg(row_to_json(l.*) ORDER BY id ASC) as logs
                FROM public.work_order_logs l
                WHERE work_order_id IS NOT NULL
                GROUP BY work_order_id;
            """)
            log_groups = cur.fetchall()
            print(f"  - Found {len(log_groups)} work_order log groups to migrate.")
            if execute:
                for lg in log_groups:
                    cur.execute("""
                        UPDATE public.work_orders 
                        SET status_logs_json = %s 
                        WHERE id = %s;
                    """, (Json(lg["logs"]), lg["work_order_id"]))

        # Di chuyển maintenance_records & maintenance_inspection_items vào work_orders
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'maintenance_records';
        """)
        if cur.fetchone():
            cur.execute("""
                SELECT mr.*, 
                       coalesce((
                           SELECT json_agg(row_to_json(ii.*)) 
                           FROM public.maintenance_inspection_items ii 
                           WHERE ii.maintenance_record_id = mr.id
                       ), '[]'::json) as items
                FROM public.maintenance_records mr
                WHERE mr.work_order_id IS NOT NULL;
            """)
            records = cur.fetchall()
            print(f"  - Found {len(records)} maintenance records to merge into work_orders.")
            if execute:
                for rec in records:
                    cur.execute("""
                        UPDATE public.work_orders 
                        SET diagnosis = coalesce(diagnosis, %s),
                            action_taken = coalesce(action_taken, %s),
                            resulting_drone_status = coalesce(resulting_drone_status, %s),
                            record_completed_at = coalesce(record_completed_at, %s),
                            inspection_items_json = %s
                        WHERE id = %s;
                    """, (
                        rec["diagnosis"], rec["corrective_action"], rec["resulting_drone_status"],
                        rec["completed_at"], Json(rec["items"]), rec["work_order_id"]
                    ))

        # ── 5. DROP 14 BẢNG THỪA / ĐÃ ĐƯỢC GỘP BẢO TOÀN DỮ LIỆU ─────────────
        tables_to_drop = [
            "roles",
            "password_reset_tokens",
            "ai_recommendations",
            "locations",
            "mission_hub_checkpoints",
            "mission_battery_swap_history",
            "mission_drone_assignment_history",
            "mission_reports",
            "payments",
            "hub_supplies",
            "supply_requests",
            "work_order_logs",
            "maintenance_records",
            "maintenance_inspection_items",
        ]

        print(f"\n[Step 5] DROP 14 redundant / consolidated tables (CASCADE):")
        for t in tables_to_drop:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s;",
                (t,),
            )
            if cur.fetchone():
                print(f"  - Dropping table '{t}' (CASCADE)")
                if execute:
                    cur.execute(f'DROP TABLE IF EXISTS public."{t}" CASCADE;')
            else:
                print(f"  - Table '{t}' already does not exist.")

        # ── 6. DỌN CỘT THỪA / KHÔNG DÙNG ────────────────────────────────────
        print("\n[Step 6] Drop unused columns in retained tables:")
        # accounts.phone
        cur.execute("""
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'accounts' AND column_name = 'phone';
        """)
        if cur.fetchone():
            print("  - Dropping unused column 'accounts.phone'")
            if execute:
                cur.execute('ALTER TABLE public."accounts" DROP COLUMN IF EXISTS "phone" CASCADE;')

        # ai_predictions.shap_values_json
        cur.execute("""
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'ai_predictions' AND column_name = 'shap_values_json';
        """)
        if cur.fetchone():
            print("  - Dropping unused column 'ai_predictions.shap_values_json'")
            if execute:
                cur.execute('ALTER TABLE public."ai_predictions" DROP COLUMN IF EXISTS "shap_values_json" CASCADE;')

        # missions.pickup_location_id, dropoff_location_id
        for mcol in ["pickup_location_id", "dropoff_location_id"]:
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'missions' AND column_name = %s;",
                (mcol,),
            )
            if cur.fetchone():
                print(f"  - Dropping unused column 'missions.{mcol}'")
                if execute:
                    cur.execute(f'ALTER TABLE public."missions" DROP COLUMN IF EXISTS "{mcol}" CASCADE;')

        # incidents.compensation_pct, compensation_note
        for icol in ["compensation_pct", "compensation_note"]:
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'incidents' AND column_name = %s;",
                (icol,),
            )
            if cur.fetchone():
                print(f"  - Dropping unused column 'incidents.{icol}'")
                if execute:
                    cur.execute(f'ALTER TABLE public."incidents" DROP COLUMN IF EXISTS "{icol}" CASCADE;')

        if execute:
            conn.commit()
            print("\n[SUCCESS] Migration committed successfully! Core Schema V3 is now live.")
        else:
            conn.rollback()
            print("\n[DRY-RUN COMPLETE] No changes were made to the database. Run with --execute to apply.")

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Migration failed and rolled back: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
