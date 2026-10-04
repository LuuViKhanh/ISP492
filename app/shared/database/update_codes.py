import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))
DATABASE_URL = os.getenv("DATABASE_URL")

async def update_database_codes():
    print("Connecting to database...")
    engine = create_async_engine(DATABASE_URL)
    
    async with engine.begin() as conn:
        print("Checking if order_code and mission_code columns exist in missions table...")
        
        # Add order_code column if not exists
        try:
            await conn.execute(text("ALTER TABLE missions ADD COLUMN order_code VARCHAR(50);"))
            print("Added order_code column to missions table.")
        except Exception as e:
            print(f"Column order_code might already exist or error: {e}")
            
        # Add mission_code column if not exists
        try:
            await conn.execute(text("ALTER TABLE missions ADD COLUMN mission_code VARCHAR(50);"))
            print("Added mission_code column to missions table.")
        except Exception as e:
            print(f"Column mission_code might already exist or error: {e}")

        print("Updating missions with ORD- and MSN- prefixes...")
        await conn.execute(text("UPDATE missions SET order_code = 'ORD-' || id WHERE order_code IS NULL;"))
        await conn.execute(text("UPDATE missions SET mission_code = 'MSN-' || id WHERE mission_code IS NULL;"))
        
        print("Updating batteries with BAT- prefix...")
        await conn.execute(text("UPDATE batteries SET serial_number = 'BAT-' || id WHERE serial_number NOT LIKE 'BAT-%';"))
        
        print("Database update completed successfully!")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(update_database_codes())
