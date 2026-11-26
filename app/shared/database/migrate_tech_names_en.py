"""Migration: Đổi full_name Technician từ tiếng Việt sang tiếng Anh."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import create_engine, text

SYNC_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql+psycopg2")
engine = create_engine(SYNC_URL, pool_pre_ping=True, connect_args={"sslmode": "require", "options": "-c search_path=public"})

TECH_NAMES = {
    "tech.benthanh@droneoptai.com":   "Technician — Ben Thanh Ward",
    "tech.vothansu@droneoptai.com":   "Technician — Vo Thi Sau Ward",
    "tech.tanphong@droneoptai.com":   "Technician — Tan Phong Ward",
    "tech.linhchieu@droneoptai.com":  "Technician — Linh Chieu Ward",
    "tech.nguyenoanh@droneoptai.com": "Technician — Ward 17, Go Vap",
    "tech.ungvankhiem@droneoptai.com":"Technician — Ward 25, Binh Thanh",
    "tech.conghoa@droneoptai.com":    "Technician — Ward 4, Tan Binh",
    "tech.anlac@droneoptai.com":      "Technician — An Lac Ward",
    "tech.nhabe@droneoptai.com":      "Technician — Nha Be Ward",
    "tech.hoathanh@droneoptai.com":   "Technician — Hoa Thanh Ward",
}

def run():
    with engine.connect() as conn:
        with conn.begin():
            for email, name in TECH_NAMES.items():
                conn.execute(text(
                    "UPDATE accounts SET full_name = :name WHERE email = :email"
                ), {"name": name, "email": email})
                print(f"  {email} -> {name}")
    print("OK — Updated technician full_names to English.")

if __name__ == "__main__":
    run()
