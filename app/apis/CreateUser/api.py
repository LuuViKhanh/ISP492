"""API layer: HTTP route only. Wires the request to the service and returns a response."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.apis.CreateUser.repository import CreateUserRepository
from app.apis.CreateUser.schema import CreateUserRequest, CreateUserResponse
from app.apis.CreateUser.service import CreateUserService
from app.common.database import get_db
from app.common.response import ApiResponse, success_response

router = APIRouter(prefix="/users", tags=["Users"])


def get_service(db: Session = Depends(get_db)) -> CreateUserService:
    return CreateUserService(repository=CreateUserRepository(db))


@router.post("", response_model=ApiResponse[CreateUserResponse], status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    service: CreateUserService = Depends(get_service),
):
    result = service.create_user(full_name=payload.full_name, email=payload.email)
    return success_response(data=result, message="User created")
