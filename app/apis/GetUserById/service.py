"""Business logic layer: validation, orchestration, calls the repository."""
from app.apis.GetUserById.repository import GetUserByIdRepository
from app.apis.GetUserById.schema import GetUserByIdResponse
from app.common.exceptions import NotFoundError


class GetUserByIdService:
    def __init__(self, repository: GetUserByIdRepository):
        self.repository = repository

    def get_user_by_id(self, user_id: int) -> GetUserByIdResponse:
        user = self.repository.get_by_id(user_id)
        if not user:
            raise NotFoundError(f"User with id {user_id} not found")
        return GetUserByIdResponse.model_validate(user)
