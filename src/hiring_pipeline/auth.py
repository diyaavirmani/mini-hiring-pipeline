"""Password verification and short-lived signed recruiter sessions."""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .database import connect


SESSION_COOKIE = "hiring_session"
SESSION_MAX_AGE_SECONDS = 60 * 60


def password_serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="mini-hiring-pipeline-session-v1")


def issue_session(secret_key: str, recruiter_id: int) -> str:
    return password_serializer(secret_key).dumps({"recruiter_id": recruiter_id})


def read_session(secret_key: str, token: str):
    try:
        session = password_serializer(secret_key).loads(
            token, max_age=SESSION_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return None
    recruiter_id = session.get("recruiter_id") if isinstance(session, dict) else None
    return recruiter_id if isinstance(recruiter_id, int) else None


def recruiter_id_for_session(secret_key: str, token: str, database_path):
    recruiter_id = read_session(secret_key, token)
    if recruiter_id is None:
        return None
    connection = connect(database_path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM recruiters WHERE id = ?", (recruiter_id,)
        ).fetchone()
        return recruiter_id if exists is not None else None
    finally:
        connection.close()


def authenticate_recruiter(email: str, password: str, database_path):
    """Return the account row after password verification, or None."""
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

    connection = connect(database_path)
    try:
        row = connection.execute(
            "SELECT id, email, password_hash FROM recruiters WHERE email = ?",
            (email.strip(),),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None
    try:
        PasswordHasher().verify(row["password_hash"], password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return None
    return {"id": row["id"], "email": row["email"]}
