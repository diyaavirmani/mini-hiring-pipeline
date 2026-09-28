"""Application configuration loaded from the environment and local .env file."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class ConfigurationError(ValueError):
    """Raised when required local application configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    app_secret_key: str
    app_env: str
    app_timezone: str
    database_path: Path
    gemini_api_key: Optional[str]
    gemini_model: str


def get_settings() -> Settings:
    secret = os.getenv("APP_SECRET_KEY", "").strip()
    if len(secret) < 32 or secret.startswith("replace-with-"):
        raise ConfigurationError(
            "APP_SECRET_KEY must contain at least 32 characters. "
            "Copy .env.example to .env and set a random value."
        )

    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env not in {"development", "test", "production"}:
        raise ConfigurationError("APP_ENV must be development, test, or production.")

    timezone = os.getenv("APP_TIMEZONE", "Asia/Kolkata").strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(
            f"APP_TIMEZONE '{timezone}' is not a recognized IANA timezone."
        ) from exc

    database_url = os.getenv(
        "DATABASE_URL", "sqlite:///./data/hiring_pipeline.sqlite3"
    ).strip()
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ConfigurationError("DATABASE_URL must use a sqlite:/// URL.")
    database_value = database_url[len(prefix) :]
    if not database_value:
        raise ConfigurationError("DATABASE_URL must include a database path.")

    database_path = Path(database_value)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", gemini_model):
        raise ConfigurationError("GEMINI_MODEL must be a model name, not a URL or path.")

    return Settings(
        app_secret_key=secret,
        app_env=app_env,
        app_timezone=timezone,
        database_path=database_path.resolve(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or None,
        gemini_model=gemini_model,
    )
