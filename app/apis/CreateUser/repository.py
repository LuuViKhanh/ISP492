"""Data access layer: talks to the DB only, no business rules here."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.models import User


class CreateUserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email))

    def create(self, full_name: str, email: str) -> User:
        user = User(full_name=full_name, email=email)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
