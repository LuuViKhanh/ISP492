from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "DroneOptAI - Energy-Efficient Drone Delivery System"
    API_V1_STR: str = "/api/v1"

    # Secret key for JWT auth (to be used by Auth module)
    SECRET_KEY: str = "tandeptrai123"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Database — điền vào file .env, ví dụ:
    # DATABASE_URL=postgresql://user:password@host:5432/dbname
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/droneoptai"

    class Config:
        env_file = ".env"

settings = Settings()
