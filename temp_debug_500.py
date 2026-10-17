import asyncio, sys, traceback
sys.stdout.reconfigure(encoding="utf-8")
from app.database.db import AsyncSessionLocal
from app.modules.missions.models import Order
from app.modules.missions.operator.router import compute_mission_routes_internal
from sqlalchemy import select

async def test():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).order_by(Order.created_at.desc()))
        orders = result.scalars().all()
        print(f"Total orders: {len(orders)}")
        for o in orders[:5]:
            print(f"Checking Order {o.id} (Origin: {o.origin_hub_id}, Dest: {o.destination_hub_id}, Payload: {o.payload_kg})")
            try:
                routes, _ = await compute_mission_routes_internal(session, o, drone_id=1)
                print(f"  -> OK! {len(routes)} routes")
            except Exception as e:
                print(f"  -> ERROR on {o.id}: {e}")
                traceback.print_exc()

asyncio.run(test())
