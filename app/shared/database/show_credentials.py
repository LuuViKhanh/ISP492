import asyncio
import sys
import os
import bcrypt

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Đảm bảo import được các module từ Drone-backend/app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from sqlalchemy import select
from app.database.db import AsyncSessionLocal
from app.modules.auth.models import User

async def show_credentials():
    """
    Script hiển thị danh sách tất cả các user mẫu trong Database,
    cho phép chọn một account để xem thông tin và đặt lại mật khẩu.
    """
    print("\n" + "="*60)
    print(" QUẢN LÝ TÀI KHOẢN ĐĂNG NHẬP (TEST CREDENTIALS) ")
    print("="*60)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        
        if not users:
            print("Không tìm thấy user nào trong Database. Hãy chạy script seed_users trước!")
            return

        print("\nDanh sách tài khoản hiện có trong hệ thống:")
        for idx, user in enumerate(users):
            role_name = user.role.value if hasattr(user, 'role') else "Unknown"
            print(f" [{idx + 1}] {user.email} (Role: {role_name}) - {user.full_name}")

        choice = input("\nNhập số thứ tự của tài khoản bạn muốn xem (hoặc gõ 'q' để thoát): ")
        if choice.lower() == 'q' or not choice.isdigit():
            return
            
        choice_idx = int(choice) - 1
        if choice_idx < 0 or choice_idx >= len(users):
            print("Lựa chọn không hợp lệ!")
            return
            
        selected_user = users[choice_idx]
        role_name = selected_user.role.value if hasattr(selected_user, 'role') else "Unknown"
        
        print("\n" + "-"*60)
        print(f" Vai trò (Role):  {role_name.upper()}")
        print(f" Họ và tên:       {selected_user.full_name}")
        print(f" Email đăng nhập: {selected_user.email}")
        print(f" Username:        {getattr(selected_user, 'username', 'Không có (Dùng email)')}")
        print(f" Chuỗi Hash Pass: {selected_user.hashed_password}")
        print("-" * 60)
        
        print("\n* LƯU Ý: Mật khẩu đã được mã hóa 1 chiều (Bcrypt) trong DB nên KHÔNG THỂ giải mã để xem mật khẩu gốc.")
        reset_choice = input("👉 Bạn có muốn ĐẶT LẠI mật khẩu của tài khoản này thành 'Password123!' để có thể đăng nhập ngay không? (y/n): ")
        
        if reset_choice.lower() == 'y':
            new_plain = "Password123!"
            new_hash = bcrypt.hashpw(new_plain.encode(), bcrypt.gensalt()).decode()
            selected_user.hashed_password = new_hash
            db.add(selected_user)
            await db.commit()
            print(f"\n✅ THÀNH CÔNG! Đã đặt lại mật khẩu cho {selected_user.email}.")
            print(f"   => Giờ bạn hãy ra Web và đăng nhập bằng mật khẩu: {new_plain}")
        else:
            print("\nĐã bỏ qua thao tác đổi mật khẩu.")
            
        print("="*60 + "\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(show_credentials())
