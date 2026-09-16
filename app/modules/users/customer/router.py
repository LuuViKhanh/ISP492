from fastapi import APIRouter

router = APIRouter(
    prefix="/customer/users",
    tags=["Customer - Users"]
)

@router.get("/")
def get_customer_users():
    return {"message": "This is customer endpoint for users"}
