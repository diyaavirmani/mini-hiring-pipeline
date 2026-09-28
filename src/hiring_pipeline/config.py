"""Application configuration loaded from the environment and local .env file."""

from dataclasses import dataclass
import os
from pathlib import Path
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

    return Settings(
        app_secret_key=secret,
        app_env=app_env,
        app_timezone=timezone,
        database_path=database_path.resolve(),
    )
