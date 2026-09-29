import asyncio
import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Đảm bảo import được các module từ Drone-backend/app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from sqlalchemy import select
from app.database.db import AsyncSessionLocal
from app.modules.auth.models import User

async def show_credentials():
    """
    Script truy xuất danh sách các user mẫu từ Database 
    để gợi nhớ thông tin đăng nhập trong trường hợp quên.
    """
    target_emails = ["admin@drone.com", "tech@example.com", "operator@droneoptai.com"]
    
    print("\n" + "="*60)
    print(" DANH SÁCH TÀI KHOẢN ĐĂNG NHẬP MẪU (TEST CREDENTIALS) ")
    print("="*60)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email.in_(target_emails)))
        users = result.scalars().all()
        
        if not users:
            print("Không tìm thấy user mẫu nào trong Database. Hãy chạy file seed_users.py trước!")
            return

        for user in users:
            role_name = user.role.value if hasattr(user, 'role') else "Unknown"
            print(f" Vai trò (Role):  {role_name.upper()}")
            print(f" Họ và tên:       {user.full_name}")
            print(f" Email đăng nhập: {user.email}")
            print(f" Username:        {user.username}")
            # Mật khẩu được mã hóa trong DB, nhưng với user mẫu ta biết chắc là Password123!
            print(f" Mật khẩu:        Password123!") 
            print("-" * 60)
            
        print("\n=> CÁCH SỬ DỤNG:")
        print("Mở Swagger UI -> Chọn API POST /api/v1/auth/login")
        print("Dùng 'Email đăng nhập' và 'Mật khẩu' ở trên để điền vào Body (JSON).")
        print("="*60 + "\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(show_credentials())
