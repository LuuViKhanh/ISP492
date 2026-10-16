import asyncio, sys, traceback
sys.stdout.reconfigure(encoding="utf-8")
from app.database.db import AsyncSessionLocal
from app.modules.missions.models import Order
from app.modules.missions.schemas import MissionPlanningAnalyzeRequest
from app.modules.missions.operator.router import analyze_mission_planning
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as session:
        # Check order ORD-20261014-0005
        order = await session.get(Order, "ORD-20261014-0005")
        if not order:
            print("Order ORD-20261014-0005 not found! Listing all orders:")
            res = await session.execute(select(Order))
            for o in res.scalars().all():
                print(f"  {o.id}: origin={o.origin_hub_id}, dest={o.destination_hub_id}, payload={o.payload_kg}, status={o.status}")
            return
            
        print(f"Found Order {order.id}:")
        print(f"  origin_hub_id: {order.origin_hub_id}")
        print(f"  destination_hub_id: {order.destination_hub_id}")
        print(f"  payload_kg: {order.payload_kg}")
        print(f"  status: {order.status}")
        
        # Test analyze with droneId=1 or other drone
        try:
            req = MissionPlanningAnalyzeRequest(orderId=order.id, droneId=1)
            # Create a mock user
            class MockUser:
                id = "operator1"
                role = "OPERATOR"
            res = await analyze_mission_planning(request=req, user=MockUser(), db=session)
            print("Analyze SUCCESS! Routes count:", len(res.routes))
            for r in res.routes:
                print(f"  Route {r.routeId}: {r.route_string} | Dist: {r.distanceKm} | Energy: {r.predictedEnergyWh}")
        except Exception as e:
            print("Analyze FAILED with error:")
            traceback.print_exc()

asyncio.run(check())
