"""API layer: HTTP route only. Wires the request to the service and returns a response."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.apis.GetUserById.repository import GetUserByIdRepository
from app.apis.GetUserById.schema import GetUserByIdResponse
from app.apis.GetUserById.service import GetUserByIdService
from app.common.database import get_db
from app.common.response import ApiResponse, success_response

router = APIRouter(prefix="/users", tags=["Users"])


def get_service(db: Session = Depends(get_db)) -> GetUserByIdService:
    return GetUserByIdService(repository=GetUserByIdRepository(db))


@router.get("/{user_id}", response_model=ApiResponse[GetUserByIdResponse])
def get_user_by_id(
    user_id: int,
    service: GetUserByIdService = Depends(get_service),
):
    result = service.get_user_by_id(user_id)
    return success_response(data=result)
