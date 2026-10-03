import re

with open('D:/works/Đồ án kỳ 2/project/New folder/Drone-backend/app/modules/orders/customer/router.py', 'r', encoding='utf-8') as f:
    content = f.read()

helper = '''def calculate_delivery_cost(dist_km: float, payload_kg: float, package_type: str, delivery_mode: DeliveryMode):
    # Determine drone size based on payload
    if payload_kg <= 1:
        base_fee = 20000
        rate_per_km = 4000
        rate_per_kg = 3000
    elif payload_kg <= 3:
        base_fee = 30000
        rate_per_km = 5000
        rate_per_kg = 4000
    elif payload_kg <= 5:
        base_fee = 45000
        rate_per_km = 6000
        rate_per_kg = 5000
    else: # XL
        base_fee = 65000
        rate_per_km = 8000
        rate_per_kg = 6000
        
    distance_fee = dist_km * rate_per_km
    payload_fee = payload_kg * rate_per_kg
    
    core_fee = base_fee + distance_fee + payload_fee
    
    # Surcharges
    is_fragile = (package_type and package_type.lower() == 'fragile')
    fragile_surcharge = core_fee * 0.15 if is_fragile else 0
    
    is_express = (delivery_mode == DeliveryMode.EXPRESS)
    service_surcharge = core_fee * 0.25 if is_express else 0
    
    # Insurance 10%
    insurance_fee = core_fee * 0.10
    
    total_fee = core_fee + fragile_surcharge + service_surcharge + insurance_fee
    service_fee_total = fragile_surcharge + service_surcharge + insurance_fee
    
    return core_fee, service_fee_total, total_fee

'''

content = content.replace('@router.post("/orders/estimate", response_model=OrderEstimateResponse)', helper + '@router.post("/orders/estimate", response_model=OrderEstimateResponse)')

old_calc = '''    # Calculate fees based on energy consumed
    express_multiplier = 1.5 if body.delivery_mode == DeliveryMode.EXPRESS else 1.0
    
    # Base calculation: e.g., 1000 VND per Wh for delivery, 200 VND for service
    delivery_fee = (energy_wh * 1000) * express_multiplier
    service_fee = energy_wh * 200'''

new_calc = '''    # Calculate fees using new logic
    core_fee, service_fee, total_fee = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=body.payload_kg,
        package_type=getattr(body, "package_type", ""),
        delivery_mode=body.delivery_mode
    )
    delivery_fee = core_fee'''

content = content.replace(old_calc, new_calc)

with open('D:/works/Đồ án kỳ 2/project/New folder/Drone-backend/app/modules/orders/customer/router.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
