import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://marketplace:marketplace@localhost:5432/marketplace"
    )
    jwt_secret: str = os.environ.get("JWT_SECRET", "")
    access_minutes: int = int(os.environ.get("ACCESS_TOKEN_MINUTES", "20"))
    refresh_days: int = int(os.environ.get("REFRESH_TOKEN_DAYS", "14"))
    order_interval: Decimal = Decimal(os.environ.get("ORDER_INTERVAL_MINUTES", "1"))
    jwt_issuer: str = "marketplace"
    jwt_audience: str = "marketplace-api"

    def __post_init__(self):
        if len(self.jwt_secret.encode()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 bytes; run make setup")
        if not 15 <= self.access_minutes <= 30 or not 7 <= self.refresh_days <= 30:
            raise ValueError("token lifetimes must match the contract")
        if not self.order_interval.is_finite() or self.order_interval < 0:
            raise ValueError("ORDER_INTERVAL_MINUTES must be non-negative")


settings = Settings()
