import asyncio
import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Đảm bảo import được các module từ Drone-backend/app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from sqlalchemy import select
from app.database.db import AsyncSessionLocal
from app.modules.auth.models import User, ROLE_NAME_TO_ID
from app.shared.roles import UserRole
from app.modules.auth.service import hash_password

async def seed_users():
    """
    Tạo sẵn 3 user mẫu với 3 quyền (roles) khác nhau trong hệ thống
    để hỗ trợ test các API phân quyền.
    """
    users_to_create = [
        {
            "email": "admin@drone.com",
            "full_name": "System Administrator",
            "username": "admin",
            "password": "Password123!",
            "role": UserRole.ADMIN,
        },
        {
            "email": "tech@drone.com",
            "full_name": "Senior Technician",
            "username": "tech",
            "password": "Password123!",
            "role": UserRole.TECHNICIAN,
        },
        {
            "email": "operator@drone.com",
            "full_name": "Flight Operator",
            "username": "operator",
            "password": "Password123!",
            "role": UserRole.OPERATOR,
        }
    ]

    async with AsyncSessionLocal() as db:
        for user_data in users_to_create:
            # Check if user already exists by email OR username
            result = await db.execute(
                select(User).where((User.email == user_data["email"]) | (User.username == user_data["username"]))
            )
            existing_user = result.scalars().first()

            if not existing_user:
                new_user = User(
                    email=user_data["email"],
                    full_name=user_data["full_name"],
                    username=user_data["username"],
                    hashed_password=hash_password(user_data["password"]),
                    role_id=ROLE_NAME_TO_ID[user_data["role"]],
                    is_active=True
                )
                db.add(new_user)
                print(f"[*] Created user: {user_data['email']} (Role: {user_data['role'].value})")
            else:
                print(f"[*] User {user_data['email']} already exists. Skipping.")

        await db.commit()
        
        print("\n" + "="*50)
        print("TẠO USER THÀNH CÔNG! HƯỚNG DẪN ĐĂNG NHẬP:")
        print("="*50)
        print("1. Mở Swagger UI (http://localhost:8000/docs)")
        print("2. Tìm đến API: POST /api/v1/auth/login")
        print("3. Bấm 'Try it out', truyền vào Request Body (JSON) sau đây:")
        print("""
        {
          "email": "admin@drone.com",
          "password": "Password123!"
        }
        """)
        print("   (Thay email bằng tech@drone.com hoặc operator@drone.com để thử role khác)")
        print("4. Bấm 'Execute'. Bạn sẽ nhận được `access_token`.")
        print("5. Nhấn nút 'Authorize' (hình ổ khóa) ở góc trên cùng của Swagger UI.")
        print("6. Paste cái `access_token` vừa nhận vào đó rồi bấm 'Authorize' là xong. Bạn đã đăng nhập thành công và có thể gọi các API yêu cầu quyền hạn!")
        print("="*50)

if __name__ == "__main__":
    # Để tránh lỗi loop trên Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(seed_users())
