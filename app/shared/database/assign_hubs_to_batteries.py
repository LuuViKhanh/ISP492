import asyncio
import os
import sys
import random

# Add project root to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, BASE_DIR)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.db import AsyncSessionLocal
from app.modules.fleet.models import Battery
from app.modules.system.models import Hub

async def main():
    print("Starting to assign hubs and charge levels to batteries...")
    
    async with AsyncSessionLocal() as session:
        # Get all hubs to assign batteries to
        hubs_result = await session.execute(select(Hub))
        hubs = hubs_result.scalars().all()
        
        if not hubs:
            print("No hubs found in database! Creating dummy hubs...")
            hub1 = Hub(code="HUB-01", name="Main Delivery Hub", latitude=40.465690, longitude=-79.788281, status="Active")
            hub2 = Hub(code="HUB-02", name="Nardo Test Hub", latitude=40.583401, longitude=-79.899775, status="Active")
            session.add_all([hub1, hub2])
            await session.commit()
            await session.refresh(hub1)
            await session.refresh(hub2)
            hubs = [hub1, hub2]
            
        hub_ids = [hub.id for hub in hubs]
        print(f"Found {len(hubs)} hubs. Hub IDs: {hub_ids}")
        
        # Get all batteries
        batteries_result = await session.execute(select(Battery))
        batteries = batteries_result.scalars().all()
        
        if not batteries:
            print("No batteries found in database! Creating dummy batteries...")
            b1 = Battery(serial_number="BATT-1001", capacity_wh=150.0, status="Active", charge_level_pct=100, current_hub_id=hub_ids[0])
            b2 = Battery(serial_number="BATT-1002", capacity_wh=150.0, status="Active", charge_level_pct=80, current_hub_id=hub_ids[0])
            b3 = Battery(serial_number="BATT-1003", capacity_wh=150.0, status="Active", charge_level_pct=45, current_hub_id=hub_ids[-1])
            session.add_all([b1, b2, b3])
            await session.commit()
            batteries = [b1, b2, b3]
        
        updated_count = 0
        for battery in batteries:
            # If current_hub_id is None, assign it to a random hub
            if battery.current_hub_id is None:
                battery.current_hub_id = random.choice(hub_ids)
                updated_count += 1
            
            # If charge_level_pct is None, assign a random charge level
            if battery.charge_level_pct is None:
                battery.charge_level_pct = random.randint(20, 100)
                
        if updated_count > 0:
            await session.commit()
            print(f"Successfully updated {updated_count} batteries with hub and charge level data.")
        else:
            # Maybe they already have hubs, let's just commit charge levels if any were updated
            await session.commit()
            print("All batteries already have a current_hub_id assigned. Charge levels filled if missing.")
            
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
