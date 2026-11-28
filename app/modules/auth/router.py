from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
import secrets
import json

from app.core.config import settings
from app.database.db import get_async_db, AsyncSessionLocal
from app.modules.auth import service
from app.modules.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse, ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest
from app.modules.system.service import log_activity
from app.shared.dependencies import get_current_user, CurrentUser

# ── Lưu trữ state tạm thời (OAuth state) ──
# Trong production, dùng Redis thay vì dict
oauth_state_store = {}


async def _log_login_activity_bg(user_id: str, user_email: str, role_value: str) -> None:
    try:
        async with AsyncSessionLocal() as bg_db:
            await log_activity(
                bg_db,
                action="LOGIN",
                user_id=user_id,
                user_email=user_email,
                role=role_value,
                resource_type="users",
                resource_id=user_id,
                description=f"Logged in successfully: {user_email}",
            )
    except Exception:
        pass

router = APIRouter(prefix="/auth", tags=["Authentication"])

GOOGLE_AUTH_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
    "?response_type=code"
    "&scope=openid%20email%20profile"
    "&client_id={client_id}"
    "&redirect_uri={redirect_uri}"
    "&state={state}"
)


# ── Register
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_async_db)):
    """
    Đăng ký người dùng mới.
    Nhận thông tin email, họ tên và mật khẩu.
    
    Quy tắc xử lý đăng ký lại:
    - Nếu email thuộc về tài khoản đang hoạt động (is_active = True): Báo lỗi Email already registered.
    - Nếu email thuộc về tài khoản đã bị xóa mềm trước đó (is_active = False):
      Hệ thống tự động lưu trữ vĩnh viễn (archive) tài khoản cũ bằng cách gắn tiền tố 'archived_{timestamp}_'
      vào email cũ để giải phóng email gốc. Sau đó tạo tài khoản mới tinh với email gốc.
      Tài khoản cũ từ thời điểm này sẽ bị ngắt hoàn toàn và không thể khôi phục (restore) được nữa.
    """
    existing_user = await service.get_user_by_email(db, body.email)
    if existing_user:
        if existing_user.is_active:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Tài khoản cũ đã bị xóa mềm -> Archive vĩnh viễn để giải phóng email gốc
        import time
        archived_email = f"archived_{int(time.time())}_{existing_user.email}"
        old_id = existing_user.id
        old_email = existing_user.email
        
        existing_user.email = archived_email
        db.add(existing_user)
        await db.flush()
        
        await log_activity(
            db, action="ARCHIVE_OLD_ACCOUNT", user_id=old_id,
            resource_type="users", resource_id=old_id,
            description=f"Archived old account {old_email} for re-registration",
            details={"old_email": old_email, "archived_email": archived_email}
        )

    user = await service.register_user(db, body.email, body.full_name, body.password)
    await log_activity(db, action="REGISTER", user_id=user.id, user_email=user.email,
                       resource_type="users", resource_id=user.id,
                       description=f"Registered new account: {user.email}")
    return user


# ── Login (email + password) ──────────────────────────────────────────────────

async def _log_failed_login_bg(
    user_id: str | None,
    user_email: str,
    role_value: str | None,
    description: str,
) -> None:
    """Ghi log đăng nhập thất bại. Không bao giờ ghi mật khẩu. Lỗi ghi log không ảnh hưởng response."""
    try:
        async with AsyncSessionLocal() as bg_db:
            await log_activity(
                bg_db,
                action="LOGIN",
                user_id=user_id,
                user_email=user_email,
                role=role_value,
                resource_type="users",
                resource_id=user_id,
                description=description,
                status="failed",
            )
    except Exception:
        pass


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Đăng nhập bằng email và mật khẩu.
    Kiểm tra thông tin đăng nhập, nếu hợp lệ sẽ trả về access token và refresh token.
    """
    user = await service.get_user_by_email(db, body.email)
    if not user or not user.hashed_password or not await service.verify_password_async(body.password, user.hashed_password):
        background_tasks.add_task(
            _log_failed_login_bg,
            user.id if user else None,
            body.email,
            user.role.value if user else None,
            "Invalid email or password",
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        background_tasks.add_task(
            _log_failed_login_bg,
            user.id,
            user.email,
            user.role.value,
            "Account inactive",
        )
        raise HTTPException(
            status_code=403, 
            detail="This account has been deleted or suspended. Please contact an administrator for assistance."
        )
    background_tasks.add_task(_log_login_activity_bg, user.id, user.email, user.role.value)
    return TokenResponse(
        access_token=service.create_access_token(user.id, user.role.value),
        refresh_token=service.create_refresh_token(user.id),
        role=user.role.value.lower(),
        must_change_password=False,
    )


# ── Forgot / Reset Password ──────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Yêu cầu đặt lại mật khẩu.
    Tạo token đặt lại mật khẩu và gửi liên kết đặt lại qua email của người dùng.
    """
    from app.core.email import send_reset_email
    token = await service.create_reset_token(db, body.email)
    if token:
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        background_tasks.add_task(send_reset_email, body.email, reset_link)
    return {"message": "If this email exists, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_async_db)):
    """
    Đặt lại mật khẩu bằng token.
    Xác minh token nhận được từ email và tiến hành cập nhật mật khẩu mới.
    """
    success = await service.reset_password(db, body.token, body.new_password)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    await log_activity(db, action="PASSWORD_RESET", description="Password reset successfully")
    return {"message": "Password reset successfully"}


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, db: AsyncSession = Depends(get_async_db), user: CurrentUser = Depends(get_current_user)):
    """
    Thay đổi mật khẩu.
    Người dùng phải cung cấp mật khẩu cũ để xác minh, sau đó nhập mật khẩu mới và xác nhận.
    """
    # Kiểm tra new_password và confirm_new_password có trùng không
    if body.new_password != body.confirm_new_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match")
    
    # Lấy user từ database
    current_user = await service.get_user_by_id(db, user.id)
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Kiểm tra mật khẩu cũ
    if not service.verify_password(body.old_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Old password is incorrect")
    
    # Cập nhật mật khẩu mới
    current_user.hashed_password = service.hash_password(body.new_password)
    db.add(current_user)
    await db.commit()
    
    await log_activity(db, action="PASSWORD_CHANGED", user_id=current_user.id, user_email=current_user.email,
                       resource_type="users", resource_id=current_user.id,
                       description=f"Password changed successfully: {current_user.email}")
    return {"message": "Password changed successfully"}


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout():
    """
    Đăng xuất người dùng.
    Client cần tự xóa token phía mình. (Sử dụng JWT stateless).
    """
    # JWT là stateless — client xóa token phía mình là đủ.
    # Nếu cần blacklist token, thêm Redis ở đây sau.
    return {"message": "Logged out successfully"}


# ── Refresh token ─────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_async_db)):
    """
    Cập nhật access token mới.
    Xác minh refresh token và trả về cặp token (access và refresh token) mới.
    """
    payload = service.decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = await service.get_user_by_id(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Account has been deleted or deactivated")
    return TokenResponse(
        access_token=service.create_access_token(user.id, user.role.value),
        refresh_token=service.create_refresh_token(user.id),
        role=user.role.value.lower(),
        must_change_password=False,
    )


# ── Google OAuth ─────────────────────────────────────────────────────────────

@router.get("/google")
async def google_login(client_type: str = None, state: str = None):
    """
    Bắt đầu quá trình đăng nhập qua Google.
    
    Query params:
    - client_type: "web" (default) hoặc "mobile" (alternative: use 'state' parameter)
    - state: "mobile" hoặc "web" (alias cho client_type, từ FE mobile)
    
    Chuyển hướng người dùng đến trang xác thực của Google kèm state parameter.
    """
    # Determine client_type từ 'state' query param hoặc 'client_type' param
    # Priority: state > client_type > default "web"
    if state and state in ("web", "mobile"):
        client_type = state
    elif client_type and client_type in ("web", "mobile"):
        pass  # Dùng client_type đã được set
    else:
        client_type = "web"  # Default
    
    # Tạo state token để bảo vệ CSRF
    csrf_state = secrets.token_urlsafe(32)
    oauth_state_store[csrf_state] = {
        "client_type": client_type,
        "timestamp": __import__("time").time()
    }
    
    # Backend URL để callback
    backend_url = settings.BACKEND_URL.rstrip("/")
    redirect_uri = f"{backend_url}/api/v1/auth/google/callback"
    
    url = GOOGLE_AUTH_URL.format(
        client_id=settings.GOOGLE_CLIENT_ID,
        redirect_uri=redirect_uri,
        state=csrf_state,
    )
    
    print(f"[AUTH] Google Login: client_type={client_type}, csrf_state={csrf_state[:8]}...")
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(code: str, state: str, db: AsyncSession = Depends(get_async_db)):
    """
    Xử lý callback từ Google sau khi xác thực thành công.
    Redirect về Deep Link (mobile) hoặc Web URL (web) kèm access_token.
    
    Query params:
    - code: Authorization code từ Google
    - state: CSRF State parameter từ Google (để xác định client type từ oauth_state_store)
    """
    print(f"[AUTH] Google Callback received: state={state[:8]}... from Google")
    
    # Kiểm tra state để xác định client type
    state_info = oauth_state_store.get(state)
    if not state_info:
        print(f"[AUTH] INVALID STATE: {state} not found in oauth_state_store")
        print(f"[AUTH] Available states: {list(oauth_state_store.keys())[:3]}...")
        return RedirectResponse(url=f"{settings.GOOGLE_CALLBACK_WEB}?error=invalid_state")
    
    # Xóa state sau khi sử dụng (one-time use)
    del oauth_state_store[state]
    
    client_type = state_info.get("client_type", "web")
    print(f"[AUTH] State resolved to client_type: {client_type}")
    
    try:
        google_info = await service.exchange_google_code(code)
        print(f"[AUTH] Google code exchanged successfully for user: {google_info.get('email')}")
    except Exception as e:
        print(f"[AUTH] Google exchange code failed: {e}")
        if client_type == "mobile":
            error_url = f"{settings.GOOGLE_CALLBACK_MOBILE}?error=google_failed"
            print(f"[AUTH] Redirect mobile error URL: {error_url}")
            return RedirectResponse(url=error_url)
        else:
            error_url = f"{settings.GOOGLE_CALLBACK_WEB}?error=google_failed"
            print(f"[AUTH] Redirect web error URL: {error_url}")
            return RedirectResponse(url=error_url)

    user = await service.get_or_create_google_user(db, google_info)
    
    access_token = service.create_access_token(user.id, user.role.value)
    refresh_token = service.create_refresh_token(user.id)
    
    # Redirect đến URL phù hợp với client type
    if client_type == "mobile":
        # Deep Link cho Mobile
        redirect_url = (
            f"{settings.GOOGLE_CALLBACK_MOBILE}"
            f"?access_token={access_token}"
            f"&refresh_token={refresh_token}"
            f"&token_type=bearer"
            f"&role={user.role.value.lower()}"
        )
        print(f"[AUTH] Mobile callback: redirect_url={redirect_url[:80]}...")
    else:
        # Web URL
        redirect_url = (
            f"{settings.GOOGLE_CALLBACK_WEB}"
            f"?access_token={access_token}"
            f"&refresh_token={refresh_token}"
            f"&token_type=bearer"
            f"&role={user.role.value.lower()}"
        )
        print(f"[AUTH] Web callback: redirect_url={redirect_url[:80]}...")
    
    return RedirectResponse(url=redirect_url)
