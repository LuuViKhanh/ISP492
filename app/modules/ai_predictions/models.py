import enum
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Float, String, DateTime, Boolean, Enum as SAEnum, Text
from sqlalchemy.dialects.postgresql import JSONB
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
    mission_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_energy_wh: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_duration_m: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, name="risk_level", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    shap_values_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[RecommendationCategory] = mapped_column(
        SAEnum(RecommendationCategory, name="category", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
