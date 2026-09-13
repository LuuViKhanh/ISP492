from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/login")
def login():
    # TODO: Người làm tính năng Auth sẽ viết logic tạo token ở đây
    return {"message": "Login logic will be implemented here by the Auth team"}

@router.post("/logout")
def logout():
    return {"message": "Logout logic"}
