import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from sqlalchemy import text
from app.database.db import engine

def migrate():
    print("Start updating Database structure for Routing & Battery Swap...")
    
    # Block 1: Add column
    with engine.connect() as conn:
        with conn.begin():
            print("1. Add battery_id to mission_legs...")
            try:
                conn.execute(text("ALTER TABLE mission_legs ADD COLUMN IF NOT EXISTS battery_id BIGINT;"))
                print("   -> Column added or already exists.")
            except Exception as e:
                print(f"   -> Failed: {e}")

    # Block 2: Add FK
    with engine.connect() as conn:
        with conn.begin():
            try:
                conn.execute(text('''
                    DO  
                    BEGIN 
                        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_mission_legs_battery') THEN 
                            ALTER TABLE mission_legs ADD CONSTRAINT fk_mission_legs_battery FOREIGN KEY (battery_id) REFERENCES batteries (id); 
                        END IF; 
                    END ;
                '''))
                print("   -> FK added or already exists.")
            except Exception as e:
                print(f"   -> FK Failed: {e}")

    # Block 3: Tables
    with engine.connect() as conn:
        with conn.begin():
            print("2. Create mission_battery_swap_history table...")
            conn.execute(text("DROP TABLE IF EXISTS mission_drone_assignment_history CASCADE;"))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS mission_battery_swap_history (
                    id SERIAL PRIMARY KEY,
                    mission_id BIGINT,
                    hub_id INT,
                    old_battery_id BIGINT,
                    new_battery_id BIGINT,
                    swapped_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            '''))
            print("   -> OK")

    print("Migration successful!")

if __name__ == '__main__':
    migrate()
