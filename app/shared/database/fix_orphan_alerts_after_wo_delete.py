"""
Fix một lần: alert từng gắn với work order đã bị xóa (bị gỡ work_order_id về NULL
bởi bản DELETE work order trước đó) đang hiện lại ở trang Maintenance alerts.

Chế độ mặc định chỉ LIỆT KÊ. Chạy với --apply để đánh dấu Dismissed.

Cách nhận diện: alert Pending, work_order_id NULL, và đã có activity/work order
từng tham chiếu alert_id này (work_orders.alert_id) nhưng WO đó không còn tồn tại
→ không xác định được qua work_orders. Vì vậy script nhận danh sách alert id
qua tham số --ids (vd: --ids 56) để tránh dismiss nhầm alert thật.
"""
import sys, os, argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import create_engine, text

SYNC_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql+psycopg2")
engine = create_engine(SYNC_URL, pool_pre_ping=True,
                       connect_args={"sslmode": "require", "options": "-c search_path=public"})


def run(ids: list[int], apply: bool):
    with engine.connect() as conn:
        with conn.begin():
            rows = conn.execute(text(
                "SELECT id, drone_id, title, status, work_order_id, created_at "
                "FROM maintenance_alerts WHERE id = ANY(:ids)"
            ), {"ids": ids}).fetchall()
            for r in rows:
                print(f"  ALT-{r.id:03d} drone={r.drone_id} status={r.status} wo={r.work_order_id} title={r.title}")
            if not apply:
                print("Dry run — chạy lại với --apply để đánh dấu Dismissed.")
                return
            conn.execute(text(
                "UPDATE maintenance_alerts SET status = 'Dismissed' "
                "WHERE id = ANY(:ids) AND work_order_id IS NULL"
            ), {"ids": ids})
            print(f"OK — Dismissed {len(rows)} alert(s).")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ids", type=int, nargs="+", required=True)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    run(a.ids, a.apply)
