import psycopg2

conn = psycopg2.connect(
    host='db.iavsnlhixjsxhuhpjyni.supabase.co',
    port=5432,
    dbname='postgres',
    user='postgres',
    password='TanMy@huntrot',
    sslmode='require'
)
cur = conn.cursor()

# Insert locations
locations = [
    ('Hub District 1', 10.7769, 106.7009, 'Hub'),
    ('Hub District 7', 10.7397, 106.7214, 'Hub'),
    ('Customer - Nguyen Van A', 10.7623, 106.6825, 'CustomerAddress'),
    ('Customer - Tran Thi B',  10.7512, 106.7103, 'CustomerAddress'),
    ('Customer - Le Van C',    10.7834, 106.6951, 'CustomerAddress'),
]
loc_ids = []
for loc in locations:
    cur.execute(
        "INSERT INTO public.locations (name, latitude, longitude, type) VALUES (%s, %s, %s, %s) RETURNING id",
        loc
    )
    loc_ids.append(cur.fetchone()[0])
print('location_ids:', loc_ids)

# customer IDs
customers = [
    'e9294b82-f4ac-414f-8f8c-f6bbf5bc022b',
    '7149ce5f-3997-4a3c-b6b2-e6ec8eb6b1ff',
    '059a76e3-2107-4acf-80ed-d2759e0f9998',
]

# Sample missions
missions = [
    # (customer_id, drone_id, battery_id, pickup_loc, dropoff_loc, payload_kg, distance_m, fee, status, scheduled)
    (customers[0], 2, 1, loc_ids[0], loc_ids[2], 1.2, 3500.0, 45000.0, 'Pending Approval', '2026-09-20 08:00:00'),
    (customers[1], 3, 2, loc_ids[1], loc_ids[3], 0.8, 2800.0, 38000.0, 'Pending Approval', '2026-09-20 09:30:00'),
    (customers[2], 4, 3, loc_ids[0], loc_ids[4], 1.5, 4200.0, 52000.0, 'Pending Approval', '2026-09-20 11:00:00'),
    (customers[0], 5, 4, loc_ids[1], loc_ids[2], 0.5, 1900.0, 28000.0, 'Approved',         '2026-09-19 14:00:00'),
    (customers[1], 6, 5, loc_ids[0], loc_ids[3], 2.0, 5100.0, 65000.0, 'Flying',           '2026-09-19 15:30:00'),
    (customers[2], 2, 1, loc_ids[1], loc_ids[4], 1.0, 3100.0, 42000.0, 'Completed',        '2026-09-18 10:00:00'),
    (customers[0], 3, 2, loc_ids[0], loc_ids[2], 0.7, 2200.0, 33000.0, 'Rejected',         '2026-09-17 09:00:00'),
    (customers[1], 4, 3, loc_ids[1], loc_ids[3], 1.3, 3800.0, 48000.0, 'Awaiting Payment', '2026-09-21 08:00:00'),
]

for m in missions:
    cur.execute("""
        INSERT INTO public.missions
        (customer_id, drone_id, battery_id, pickup_location_id, dropoff_location_id,
         payload_weight, distance_m, delivery_fee, status, scheduled_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, m)
    print('mission id:', cur.fetchone()[0], '| status:', m[8])

conn.commit()
conn.close()
print('Done!')
