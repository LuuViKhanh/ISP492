from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def run_maintenance_alerts_logic(db: AsyncSession):
    query = text("""
        INSERT INTO maintenance_alerts (drone_id, source, maintenance_schedule_id, title, status)
        SELECT drone_id, 'System', id, 'Lịch bảo trì định kỳ: ' || maintenance_type, 'Pending'
        FROM maintenance_schedules
        WHERE next_inspection_at <= NOW()
          AND status = 'Active'
          AND NOT EXISTS (
              SELECT 1 FROM maintenance_alerts 
              WHERE maintenance_alerts.maintenance_schedule_id = maintenance_schedules.id 
                AND maintenance_alerts.status = 'Pending'
          )
        RETURNING id;
    """)
    result = await db.execute(query)
    inserted_ids = [row[0] for row in result.fetchall()]
    await db.commit()
    return inserted_ids

async def run_battery_overdue_alerts_logic(db: AsyncSession):
    query_battery = text("""
        INSERT INTO maintenance_alerts (drone_id, source, title, status)
        SELECT id, 'System', 'Cảnh báo Pin thấp (' || battery_level_pct || '%)', 'Pending'
        FROM drones
        WHERE battery_level_pct <= 20
          AND NOT EXISTS (
              SELECT 1 FROM maintenance_alerts 
              WHERE maintenance_alerts.drone_id = drones.id 
                AND maintenance_alerts.title LIKE 'Cảnh báo Pin thấp%'
                AND maintenance_alerts.status = 'Pending'
          )
        RETURNING id;
    """)
    res_batt = await db.execute(query_battery)
    batt_ids = [row[0] for row in res_batt.fetchall()]
    
    query_overdue = text("""
        INSERT INTO maintenance_alerts (drone_id, source, title, status)
        SELECT drone_id, 'System', 'Cảnh báo Quá hạn bảo trì: ' || maintenance_type, 'Pending'
        FROM maintenance_schedules
        WHERE next_inspection_at <= NOW() - INTERVAL '7 days'
          AND status = 'Active'
          AND NOT EXISTS (
              SELECT 1 FROM maintenance_alerts 
              WHERE maintenance_alerts.maintenance_schedule_id = maintenance_schedules.id 
                AND maintenance_alerts.title LIKE 'Cảnh báo Quá hạn%'
                AND maintenance_alerts.status = 'Pending'
          )
        RETURNING id;
    """)
    res_overdue = await db.execute(query_overdue)
    overdue_ids = [row[0] for row in res_overdue.fetchall()]
    
    await db.commit()
    return batt_ids, overdue_ids
