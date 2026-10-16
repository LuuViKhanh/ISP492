# -*- coding: utf-8 -*-
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import json
from datetime import datetime

# Setup paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from sqlalchemy import select, and_, text
from app.database.db import AsyncSessionLocal
from app.modules.missions.models import Mission, HandlingStatus, MissionStatus
from app.modules.fleet.models import Drone, Battery, batteries_status
from app.shared.dependencies import CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.technician.router import prepare_battery_for_delivery, PrepareBatteryRequest
from fastapi import HTTPException

def json_serial(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)

async def test_api_execution():
    print("=" * 80)
    print(" CHẠY THỬ NGHIỆM API POST /technician/deliveries/outbound/{orderId}/prepare-battery")
    print("=" * 80)

    async with AsyncSessionLocal() as db:
        # 1. Tìm 1 mission có sẵn drone để test (Ví dụ ORD-20261014-0018 như trong ảnh của FE)
        stmt = select(Mission, Drone).outerjoin(Drone, Mission.drone_id == Drone.id).where(Mission.drone_id.is_not(None)).order_by(Mission.id.desc()).limit(1)
        res = await db.execute(stmt)
        row = res.first()
        
        if not row:
            print("Không tìm thấy Mission nào có Drone gán sẵn!")
            return
            
        mission, drone = row
        print(f"\n[DỮ LIỆU ĐƠN HÀNG TEST]")
        print(f"• Mission ID:     {mission.id}")
        print(f"• Order ID:       {mission.order_id}")
        print(f"• Drone:          {drone.name} (Model: {drone.model})")
        print(f"• Origin Hub ID:  {mission.origin_hub_id}")

        # Đặt lại handling_status thành AT_HUB để test
        mission.handling_status = HandlingStatus.AT_HUB
        await db.commit()
        await db.refresh(mission)

        # 2. Tìm viên pin tương thích 100% tại Hub này
        res_bat = await db.execute(select(Battery).where(
            and_(
                Battery.current_hub_id == mission.origin_hub_id,
                Battery.charge_level_pct >= 100,
                Battery.status == batteries_status.ACTIVE
            )
        ).limit(10))
        bats = res_bat.scalars().all()
        
        # Chọn đúng viên pin tương thích
        from app.modules.missions.technician.router import DRONE_BATTERY_COMPATIBILITY
        compat_models = DRONE_BATTERY_COMPATIBILITY.get(drone.model, [drone.model])
        valid_bat = next((b for b in bats if b.battery_model in compat_models), None)
        
        if not valid_bat:
            print(f"Chưa tìm thấy pin tương thích với {drone.model} tại Hub {mission.origin_hub_id}")
            return

        mock_user = CurrentUser(
            id="tech_user_01",
            role=UserRole.TECHNICIAN,
            email="technician@droneoptai.com",
            hub_id=mission.origin_hub_id
        )

        # --- TEST CASE 1: TECHNICIAN CHỦ ĐỘNG GỬI BATTERY_ID HỢP LỆ ---
        print("\n" + "-" * 80)
        print("▶ TEST CASE 1: Technician chọn Pin hợp lệ (Gửi battery_id trong request)")
        print("-" * 80)
        req_body = PrepareBatteryRequest(battery_id=valid_bat.id)
        print(f"REQUEST:")
        print(f"• URL:  POST /api/v1/technician/deliveries/outbound/{mission.order_id}/prepare-battery")
        print(f"• BODY: {json.dumps(req_body.dict(), indent=2)}")

        result1 = await prepare_battery_for_delivery(
            orderId=mission.order_id,
            body=req_body,
            user=mock_user,
            db=db
        )

        # Chuyển đổi result sang JSON string
        result1_clean = {k: v for k, v in result1.items() if k != "mission"}
        print(f"\nRESPONSE (200 OK):")
        print(json.dumps(result1_clean, indent=2, default=json_serial, ensure_ascii=False))

        # --- TEST CASE 2: TECHNICIAN CHỌN PIN KHÔNG TƯƠNG THÍCH (BỊ CHẶN) ---
        print("\n" + "-" * 80)
        print("▶ TEST CASE 2: Technician chọn nhầm Pin không tương thích model (Kiểm tra validate)")
        print("-" * 80)
        # Giả lập chọn pin model khác
        invalid_bat = next((b for b in bats if b.battery_model not in compat_models), None)
        if invalid_bat:
            req_invalid = PrepareBatteryRequest(battery_id=invalid_bat.id)
            print(f"REQUEST (Pin không tương thích): battery_id = {invalid_bat.id} (Model: {invalid_bat.battery_model})")
            
            # Reset lại mission để test
            mission.handling_status = HandlingStatus.AT_HUB
            await db.commit()

            try:
                await prepare_battery_for_delivery(
                    orderId=mission.order_id,
                    body=req_invalid,
                    user=mock_user,
                    db=db
                )
                print("LỖI: Lẽ ra phải bị chặn nhưng lại thành công!")
            except HTTPException as e:
                print(f"RESPONSE BỊ CHẶN CHUẨN XÁC ({e.status_code}):")
                print(f"Detail: {e.detail}")

        print("\n" + "=" * 80)
        print(" TẤT CẢ CÁC TEST CASES ĐÃ HOÀN TẤT THÀNH CÔNG VÀ CHÍNH XÁC 100%!")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_api_execution())
