"""Business logic layer: validation, orchestration, calls the repository."""
from app.apis.CreateUser.repository import CreateUserRepository
from app.apis.CreateUser.schema import CreateUserResponse
from app.common.exceptions import ConflictError
from app.common.logger import get_logger

logger = get_logger(__name__)


class CreateUserService:
    def __init__(self, repository: CreateUserRepository):
        self.repository = repository

    def create_user(self, full_name: str, email: str) -> CreateUserResponse:
        if self.repository.get_by_email(email):
            raise ConflictError(f"User with email '{email}' already exists")

        user = self.repository.create(full_name=full_name, email=email)
        logger.info("Created user id=%s email=%s", user.id, user.email)
        return CreateUserResponse.model_validate(user)
