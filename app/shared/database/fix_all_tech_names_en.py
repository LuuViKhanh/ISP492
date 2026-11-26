"""Fix: Đổi toàn bộ Technician full_name từ tiếng Việt sang tiếng Anh."""
import sys, os, re
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import create_engine, text

SYNC_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql+psycopg2")
engine = create_engine(SYNC_URL, pool_pre_ping=True,
                       connect_args={"sslmode": "require", "options": "-c search_path=public"})

# Map tiếng Việt → tiếng Anh cho các từ thường gặp
VN_TO_EN = [
    ("Kỹ thuật viên", "Technician"),
    # Wards
    ("Phường Bến Thành", "Ben Thanh Ward"),
    ("Phường Võ Thị Sáu", "Vo Thi Sau Ward"),
    ("Phường Tân Phong", "Tan Phong Ward"),
    ("Phường Linh Chiểu", "Linh Chieu Ward"),
    ("Phường 17, Gò Vấp", "Ward 17, Go Vap"),
    ("Phường 25, Bình Thạnh", "Ward 25, Binh Thanh"),
    ("Phường 4, Tân Bình", "Ward 4, Tan Binh"),
    ("Phường An Lạc", "An Lac Ward"),
    ("Phường Nhà Bè", "Nha Be Ward"),
    ("Phường Hòa Thạnh", "Hoa Thanh Ward"),
    ("Phường Sài Gòn", "Saigon Ward"),
    ("Phường Tam Thắng", "Tam Thang Ward"),
    ("Phường Bà Rịa", "Ba Ria Ward"),
    ("Phường Vũng Tàu", "Vung Tau Ward"),
    ("Phường Xuân Hòa", "Xuan Hoa Ward"),
    ("Phường An Đông", "An Dong Ward"),
    ("Phường Bàn Cờ", "Ban Co Ward"),
    ("Phường Gia Định", "Gia Dinh Ward"),
    ("Phường Hòa Hưng", "Hoa Hung Ward"),
    ("Phường Thủ Đức", "Thu Duc Ward"),
    ("Phường Khánh Hội", "Khanh Hoi Ward"),
    ("Phường Tân Hưng", "Tan Hung Ward"),
    ("Phường Bình Hưng Hòa", "Binh Hung Hoa Ward"),
    ("Phường Tân Thới Nhất", "Tan Thoi Nhat Ward"),
    ("Phường Hiệp Bình", "Hiep Binh Ward"),
    ("Phường Linh Trung", "Linh Trung Ward"),
    ("Phường Bình Chiểu", "Binh Chieu Ward"),
    # Communes / Areas
    ("Xã Bình Khánh", "Binh Khanh Commune"),
    ("Xã An Thới Đông", "An Thoi Dong Commune"),
    ("Khu vực Long Hòa (Xã Cần Giờ)", "Long Hoa Area (Can Gio)"),
    ("Khu vực Cần Thạnh (Xã Cần Giờ)", "Can Thanh Area (Can Gio)"),
    ("Hub Bến Thành (Ca 2)", "Ben Thanh Hub (Shift 2)"),
    # Generic replacements
    ("Phường", "Ward"),
    ("Xã", "Commune"),
    ("Khu vực", "Area"),
    # Diacritics cleanup for remaining ward names
    ("Tam Thắng", "Tam Thang"),
    ("Bà Rịa", "Ba Ria"),
    ("An Đông", "An Dong"),
    ("Bàn Cờ", "Ban Co"),
    ("Gia Định", "Gia Dinh"),
    ("Hòa Hưng", "Hoa Hung"),
    ("Vũng Tàu", "Vung Tau"),
    ("Xuân Hòa", "Xuan Hoa"),
    ("Thủ Đức", "Thu Duc"),
    ("Khánh Hội", "Khanh Hoi"),
    ("Tân Hưng", "Tan Hung"),
]

def translate(name: str) -> str:
    for vn, en in VN_TO_EN:
        name = name.replace(vn, en)
    return name

def run():
    with engine.connect() as conn:
        with conn.begin():
            # Lấy tất cả account có role Technician (role_id chứa 'technician')
            rows = conn.execute(text(
                "SELECT id, email, full_name FROM accounts WHERE LOWER(role_id) LIKE '%technician%'"
            )).fetchall()

            updated = 0
            for acc_id, email, full_name in rows:
                new_name = translate(full_name)
                if new_name != full_name:
                    conn.execute(text(
                        "UPDATE accounts SET full_name = :name WHERE id = :id"
                    ), {"name": new_name, "id": acc_id})
                    print(f"  {email}: '{full_name}' -> '{new_name}'")
                    updated += 1
                else:
                    print(f"  {email}: already OK — '{full_name}'")

    print(f"\nOK — Updated {updated} technician name(s).")

if __name__ == "__main__":
    run()
