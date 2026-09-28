"""Idempotently load fictional candidates and histories from JSON."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from .config import ConfigurationError, PROJECT_ROOT, get_settings
from .database import apply_migrations, connect
from .pipeline import FINAL_STAGES, FORWARD_STAGE, Stage


DEFAULT_FIXTURE = PROJECT_ROOT / "data" / "sample_candidates.json"
VALID_STAGES = {stage.value for stage in Stage}


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _event_timestamp(spec: dict, now: datetime, zone: ZoneInfo) -> str:
    if "days_ago" in spec:
        days_ago = spec["days_ago"]
        if not isinstance(days_ago, int) or days_ago < 0:
            raise ValueError("Event days_ago must be a non-negative integer.")
        return _utc_timestamp(now - timedelta(days=days_ago))
    if spec.get("when") == "most_recent_monday":
        local_now = now.astimezone(zone)
        monday = local_now.date() - timedelta(days=local_now.weekday())
        local_midnight = datetime.combine(monday, datetime.min.time(), tzinfo=zone)
        return _utc_timestamp(local_midnight)
    raise ValueError("Each history event needs days_ago or when='most_recent_monday'.")


def _validate_candidate(candidate: dict) -> None:
    name = candidate.get("full_name")
    email = candidate.get("email")
    history = candidate.get("history")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Each sample candidate needs a non-empty full_name.")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("Each sample candidate needs a unique email.")
    if not isinstance(history, list) or not history:
        raise ValueError(f"Sample candidate {email} must have a non-empty history.")

    previous_stage = None
    for index, event in enumerate(history):
        stage = event.get("stage")
        if stage not in VALID_STAGES:
            raise ValueError(f"Unknown stage {stage!r} in sample candidate {email}.")
        if index == 0 and stage != Stage.APPLIED.value:
            raise ValueError(f"Sample candidate {email} must begin in Applied.")
        if index > 0:
            allowed_next = FORWARD_STAGE.get(previous_stage)
            if stage != Stage.REJECTED.value and stage != allowed_next:
                raise ValueError(f"Invalid sample transition {previous_stage} -> {stage}.")
            if previous_stage in FINAL_STAGES:
                raise ValueError(f"Sample candidate {email} has history after a final stage.")
        previous_stage = stage


def seed_sample_data(
    database_path: Path,
    fixture_path: Path = DEFAULT_FIXTURE,
    actor_id: Optional[int] = None,
    now: Optional[datetime] = None,
    timezone_name: str = "Asia/Kolkata",
) -> Dict[str, int]:
    """Insert any absent fixture candidates and their histories atomically.

    Email is the fixture's stable key. Existing candidates are left untouched,
    so rerunning this command neither duplicates nor rewrites recruiter data.
    """
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("candidates"), list):
        raise ValueError("Unsupported or malformed sample candidate fixture.")
    candidates = payload["candidates"]
    seen_emails = set()
    for candidate in candidates:
        _validate_candidate(candidate)
        normalized_email = candidate["email"].strip().casefold()
        if normalized_email in seen_emails:
            raise ValueError(f"Duplicate sample email in fixture: {candidate['email']}.")
        seen_emails.add(normalized_email)

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Seed clock must be timezone-aware.")
    zone = ZoneInfo(timezone_name)
    prepared = []
    for candidate in candidates:
        timestamps = [
            _event_timestamp(event, moment, zone) for event in candidate["history"]
        ]
        parsed = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in timestamps]
        if any(later <= earlier for earlier, later in zip(parsed, parsed[1:])):
            raise ValueError(
                f"Sample history timestamps must strictly increase for {candidate['email']}."
            )
        prepared.append((candidate, timestamps))

    connection = connect(database_path)
    created = 0
    skipped = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        if actor_id is None:
            recruiter = connection.execute(
                "SELECT id FROM recruiters ORDER BY id LIMIT 1"
            ).fetchone()
            if recruiter is None:
                raise RuntimeError("Create a recruiter account before seeding sample data.")
            actor_id = recruiter["id"]
        else:
            recruiter = connection.execute(
                "SELECT 1 FROM recruiters WHERE id = ?", (actor_id,)
            ).fetchone()
            if recruiter is None:
                raise RuntimeError("Seeder actor does not match an existing recruiter.")

        for candidate, timestamps in prepared:
            existing = connection.execute(
                "SELECT id FROM candidates WHERE email = ?", (candidate["email"].strip(),)
            ).fetchone()
            if existing is not None:
                skipped += 1
                continue

            cursor = connection.execute(
                """INSERT INTO candidates(full_name, email, phone, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    candidate["full_name"].strip(),
                    candidate["email"].strip(),
                    candidate.get("phone"),
                    timestamps[0],
                ),
            )
            candidate_id = cursor.lastrowid
            previous_stage = None
            for event, timestamp in zip(candidate["history"], timestamps):
                connection.execute(
                    """INSERT INTO candidate_stage_events
                       (candidate_id, actor_id, from_stage, to_stage, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        candidate_id,
                        actor_id,
                        previous_stage,
                        event["stage"],
                        event.get("reason"),
                        timestamp,
                    ),
                )
                previous_stage = event["stage"]
            created += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"created": created, "skipped": skipped}


def main() -> None:
    try:
        settings = get_settings()
        apply_migrations(settings.database_path)
        result = seed_sample_data(
            settings.database_path,
            timezone_name=settings.app_timezone,
        )
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Sample data was not loaded: {exc}") from exc
    print("Sample data loaded: {created} created, {skipped} already present.".format(**result))


if __name__ == "__main__":
    main()
