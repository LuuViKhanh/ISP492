import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from sqlalchemy import text
from app.database.db import engine

def undo():
    print("Start undoing Database structure for Routing & Battery Swap...")
    
    with engine.connect() as conn:
        with conn.begin():
            print("1. Restore mission_drone_assignment_history...")
            conn.execute(text("DROP TABLE IF EXISTS mission_battery_swap_history CASCADE;"))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS mission_drone_assignment_history (
                    id SERIAL PRIMARY KEY,
                    mission_id BIGINT,
                    old_drone_id BIGINT,
                    new_drone_id BIGINT,
                    reason TEXT,
                    replaced_by VARCHAR(50),
                    replaced_at TIMESTAMP
                );
            '''))
            print("   -> OK")

            print("2. Remove battery_id from mission_legs...")
            try:
                conn.execute(text("ALTER TABLE mission_legs DROP COLUMN IF EXISTS battery_id;"))
                print("   -> OK")
            except Exception as e:
                print(f"   -> Failed to drop column: {e}")

    print("Undo successful!")

if __name__ == '__main__':
    undo()
