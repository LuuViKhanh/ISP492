import re

with open('D:/works/Đồ án kỳ 2/project/New folder/Drone-backend/app/modules/orders/customer/router.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_create = '''    new_order = Order(
        id=order_id,
        customer_id=current_user.id,
        package_type=body.package_type,
        payload_kg=body.payload_kg,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        delivery_mode=body.delivery_mode,
        requested_delivery_at=body.requested_delivery_at.replace(tzinfo=None) if body.requested_delivery_at else None,
        status=OrderStatus.PENDING, 
        created_at=now,
        updated_at=now
    )'''

new_create = '''    origin_hub = await db.get(Hub, body.origin_hub_id)
    dest_hub = await db.get(Hub, body.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=404, detail="Hub not found")
        
    lat1, lon1 = origin_hub.latitude or 0.0, origin_hub.longitude or 0.0
    lat2, lon2 = dest_hub.latitude or 0.0, dest_hub.longitude or 0.0
    dist_km = haversine_distance(lat1, lon1, lat2, lon2)
    
    core_fee, service_fee, total_fee = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=body.payload_kg,
        package_type=body.package_type,
        delivery_mode=body.delivery_mode
    )

    new_order = Order(
        id=order_id,
        customer_id=current_user.id,
        package_type=body.package_type,
        payload_kg=body.payload_kg,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        delivery_mode=body.delivery_mode,
        requested_delivery_at=body.requested_delivery_at.replace(tzinfo=None) if body.requested_delivery_at else None,
        status=OrderStatus.PENDING, 
        delivery_fee=total_fee,
        created_at=now,
        updated_at=now
    )'''

content = content.replace(old_create, new_create)

with open('D:/works/Đồ án kỳ 2/project/New folder/Drone-backend/app/modules/orders/customer/router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
