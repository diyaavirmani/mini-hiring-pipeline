"""Command-line entry point for applying SQLite schema migrations."""

from .config import ConfigurationError, get_settings
from .database import apply_migrations


def main() -> None:
    try:
        settings = get_settings()
        applied = apply_migrations(settings.database_path)
    except (ConfigurationError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Migration failed: {exc}") from exc

    if applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("Database schema is already up to date.")


if __name__ == "__main__":
    main()
