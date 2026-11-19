import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SYNC_DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg", "postgresql")

print("Connecting to database...")
print(f"URL (masked): postgresql://...@{SYNC_DATABASE_URL.split('@')[1][:30]}...")

try:
    engine = create_engine(SYNC_DATABASE_URL, connect_args={"sslmode": "require"})
    with engine.connect() as conn:
        # Query information_schema for work_orders table structure
        result = conn.execute(text("""
            SELECT column_name, data_type, is_nullable, column_default 
            FROM information_schema.columns 
            WHERE table_name = 'work_orders' 
            ORDER BY ordinal_position
        """))
        
        print("\n========== ACTUAL work_orders TABLE SCHEMA ==========\n")
        print(f"{'Column Name':<25} {'Type':<20} {'Nullable':<12} {'Default':<30}")
        print("-" * 90)
        
        columns = result.fetchall()
        if columns:
            for col_name, col_type, nullable, default in columns:
                null_str = "YES" if nullable == "YES" else "NO"
                default_str = default if default else "None"
                print(f"{col_name:<25} {col_type:<20} {null_str:<12} {default_str:<30}")
        else:
            print("No columns found - table may not exist")
            
except Exception as e:
    print(f"Database Error: {type(e).__name__}: {e}")
