"""Create the single recruiter account used by the local application."""

import getpass
import re
import sqlite3

from argon2 import PasswordHasher

from .config import ConfigurationError, get_settings
from .database import apply_migrations, connect


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main() -> None:
    try:
        settings = get_settings()
        apply_migrations(settings.database_path)
    except (ConfigurationError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Cannot initialize recruiter account: {exc}") from exc

    email = input("Recruiter email: ").strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise SystemExit("Enter a valid email address.")
    password = getpass.getpass("Password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters.")
    if password != confirmation:
        raise SystemExit("Passwords did not match.")

    connection = connect(settings.database_path)
    try:
        existing = connection.execute("SELECT 1 FROM recruiters LIMIT 1").fetchone()
        if existing is not None:
            raise SystemExit("A recruiter account already exists for this one-recruiter app.")
        connection.execute(
            "INSERT INTO recruiters(email, password_hash) VALUES (?, ?)",
            (email, PasswordHasher().hash(password)),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise SystemExit("Recruiter account could not be created.") from exc
    finally:
        connection.close()
    print("Recruiter account created.")


if __name__ == "__main__":
    main()
