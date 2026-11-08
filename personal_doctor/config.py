import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass
class Settings:
    database_url: str = "sqlite:///data/doctor.db"
    garmin_key: str = field(default="", repr=False)
    origin: str = "http://127.0.0.1:8000"
    environment: str = "development"
    local_auth_bypass: bool = False
    password_hash: str = field(default="", repr=False)
    timezone: str = "Australia/Brisbane"
    request_interval: float = 1.0
    sync_interval: int = 3600
    recent_days: int = 7
    worker_enabled: bool = True

    @property
    def production(self):
        return self.environment == "production"

    def today(self):
        return datetime.now(ZoneInfo(self.timezone)).date()

    def validate(self):
        ZoneInfo(self.timezone)
        url = urlparse(self.origin)
        if url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("APP_ORIGIN must be an origin without a path, query or fragment")
        if self.production:
            if self.local_auth_bypass or not self.password_hash or url.scheme != "https":
                raise ValueError("Production requires HTTPS, OWNER_PASSWORD_HASH, and authentication enabled")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
        if self.local_auth_bypass and url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Local sign-in bypass requires a loopback origin")
        if self.request_interval < 0 or self.sync_interval < 300 or not 1 <= self.recent_days <= 31:
            raise ValueError("Invalid sync interval or overlap")
        return self


def load_settings():
    load_dotenv(override=False)
    Path("data").mkdir(mode=0o700, exist_ok=True)
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/doctor.db"),
        garmin_key=os.getenv("GARMIN_PERSONAL_API_KEY", ""),
        origin=os.getenv("APP_ORIGIN", "http://127.0.0.1:8000").rstrip("/"),
        environment=os.getenv("APP_ENV", "development"),
        local_auth_bypass=os.getenv("LOCAL_AUTH_BYPASS", "false").lower() == "true",
        password_hash=os.getenv("OWNER_PASSWORD_HASH", ""),
        timezone=os.getenv("APP_TIMEZONE", "Australia/Brisbane"),
        request_interval=float(os.getenv("GARMIN_REQUEST_INTERVAL_SECONDS", "1")),
        sync_interval=int(os.getenv("SYNC_INTERVAL_SECONDS", "3600")),
        recent_days=int(os.getenv("SYNC_OVERLAP_DAYS", "7")),
        worker_enabled=os.getenv("WORKER_ENABLED", "true").lower() == "true",
    ).validate()
