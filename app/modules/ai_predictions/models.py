import enum
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Float, String, DateTime, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base

class RiskLevel(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"

class RecommendationCategory(str, enum.Enum):
    MISSION_PLANNING = "Mission Planning"
    MAINTENANCE = "Maintenance"
    BATTERY_HEALTH = "Battery Health"
    PAYLOAD_OPTIMISATION = "Payload Optimisation"
    WEATHER_ALERT = "Weather Alert"
    RISK_ASSESSMENT = "Risk Assessment"
    OPERATOR_ASSIGNMENT = "Operator Assignment"

class AIPrediction(Base):
    __tablename__ = "ai_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mission_id: Mapped[str] = mapped_column(String(50), nullable=False)
    estimated_energy_wh: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_duration_m: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="Low")
    recommendation_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_recommendation_applied: Mapped[bool] = mapped_column(Boolean, default=True)
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
