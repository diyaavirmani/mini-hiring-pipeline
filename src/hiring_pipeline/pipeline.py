"""Candidate creation, stage movement, and immutable audit-history operations."""

from enum import Enum
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional

from .database import connect


class Stage(str, Enum):
    APPLIED = "Applied"
    SCREENING = "Screening"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    HIRED = "Hired"
    REJECTED = "Rejected"


FORWARD_STAGE = {
    Stage.APPLIED.value: Stage.SCREENING.value,
    Stage.SCREENING.value: Stage.INTERVIEW.value,
    Stage.INTERVIEW.value: Stage.OFFER.value,
    Stage.OFFER.value: Stage.HIRED.value,
}
FINAL_STAGES = {Stage.HIRED.value, Stage.REJECTED.value}
ALL_STAGES = {stage.value for stage in Stage}


class PipelineError(Exception):
    """Base class for expected candidate-pipeline errors."""


class CandidateNotFound(PipelineError):
    pass


class InvalidStage(PipelineError):
    pass


class InvalidTransition(PipelineError):
    pass


class StageConflict(PipelineError):
    """The candidate changed since the caller last read its stage."""


class FinalStageError(PipelineError):
    pass


class PipelineIntegrityError(PipelineError):
    pass


class DuplicateCandidate(PipelineError):
    pass


def _current_stage(connection: sqlite3.Connection, candidate_id: int) -> str:
    candidate = connection.execute(
        "SELECT 1 FROM candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if candidate is None:
        raise CandidateNotFound(f"Candidate {candidate_id} was not found.")
    row = connection.execute(
        """SELECT to_stage FROM candidate_stage_events
           WHERE candidate_id = ? ORDER BY id DESC LIMIT 1""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise PipelineIntegrityError(
            f"Candidate {candidate_id} has no initial stage event."
        )
    return row["to_stage"]


def _validate_expected_stage(expected_stage: str) -> None:
    if expected_stage not in ALL_STAGES:
        raise InvalidStage(f"Unknown candidate stage: {expected_stage!r}.")


def create_candidate(
    actor_id: int,
    full_name: str,
    database_path: Optional[Path] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> Dict[str, object]:
    """Create a candidate and their initial Applied event atomically."""
    cleaned_name = full_name.strip()
    if not cleaned_name:
        raise ValueError("Candidate name must not be empty.")

    connection = connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "INSERT INTO candidates(full_name, email, phone) VALUES (?, ?, ?)",
            (cleaned_name, email.strip() if email and email.strip() else None, phone),
        )
        candidate_id = cursor.lastrowid
        connection.execute(
            """INSERT INTO candidate_stage_events
               (candidate_id, actor_id, from_stage, to_stage, reason)
               VALUES (?, ?, NULL, ?, NULL)""",
            (candidate_id, actor_id, Stage.APPLIED.value),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        if "candidates.email" in str(exc):
            raise DuplicateCandidate("A candidate with this email already exists.") from exc
        raise PipelineIntegrityError(
            "Candidate could not be created. Check recruiter and candidate details."
        ) from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"id": candidate_id, "full_name": cleaned_name, "stage": Stage.APPLIED.value}


def advance_candidate(
    candidate_id: int,
    actor_id: int,
    expected_stage: str,
    database_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Advance one stage, rejecting stale requests and terminal candidates."""
    _validate_expected_stage(expected_stage)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current_stage = _current_stage(connection, candidate_id)
        if current_stage != expected_stage:
            raise StageConflict(
                f"Candidate is now in {current_stage}; reload before moving them."
            )
        if current_stage in FINAL_STAGES:
            raise FinalStageError(f"{current_stage} is a final outcome.")
        next_stage = FORWARD_STAGE.get(current_stage)
        if next_stage is None:
            raise InvalidTransition(f"Candidate cannot advance from {current_stage}.")

        connection.execute(
            """INSERT INTO candidate_stage_events
               (candidate_id, actor_id, from_stage, to_stage, reason)
               VALUES (?, ?, ?, ?, NULL)""",
            (candidate_id, actor_id, current_stage, next_stage),
        )
        connection.commit()
        return {"candidate_id": candidate_id, "from_stage": current_stage, "to_stage": next_stage}
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise PipelineIntegrityError(
            "Stage move was rejected by a database integrity rule."
        ) from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reject_candidate(
    candidate_id: int,
    actor_id: int,
    expected_stage: str,
    reason: Optional[str] = None,
    database_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Reject a candidate from any non-Hired stage, recording an audit event."""
    _validate_expected_stage(expected_stage)
    connection = connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current_stage = _current_stage(connection, candidate_id)
        if current_stage != expected_stage:
            raise StageConflict(
                f"Candidate is now in {current_stage}; reload before rejecting them."
            )
        if current_stage in FINAL_STAGES:
            raise FinalStageError(f"{current_stage} is a final outcome.")

        connection.execute(
            """INSERT INTO candidate_stage_events
               (candidate_id, actor_id, from_stage, to_stage, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                candidate_id,
                actor_id,
                current_stage,
                Stage.REJECTED.value,
                reason.strip() if reason and reason.strip() else None,
            ),
        )
        connection.commit()
        return {
            "candidate_id": candidate_id,
            "from_stage": current_stage,
            "to_stage": Stage.REJECTED.value,
        }
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise PipelineIntegrityError(
            "Rejection was rejected by a database integrity rule."
        ) from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_candidate_history(
    candidate_id: int, database_path: Optional[Path] = None
) -> List[Dict[str, object]]:
    """Return the complete audit history, oldest event first."""
    connection = connect(database_path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if exists is None:
            raise CandidateNotFound(f"Candidate {candidate_id} was not found.")
        rows = connection.execute(
            """SELECT id, candidate_id, actor_id, from_stage, to_stage, reason, created_at
               FROM candidate_stage_events WHERE candidate_id = ? ORDER BY id""",
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_candidate_stage(
    candidate_id: int, database_path: Optional[Path] = None
) -> str:
    """Return the current stage for a candidate."""
    connection = connect(database_path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if exists is None:
            raise CandidateNotFound(f"Candidate {candidate_id} was not found.")
        return _current_stage(connection, candidate_id)
    finally:
        connection.close()


def _stage_elapsed_seconds(created_at: str) -> float:
    from datetime import datetime, timezone

    entered_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return max(0.0, (datetime.now(timezone.utc) - entered_at).total_seconds())


def _candidate_payload(
    row: sqlite3.Row,
    include_history: bool,
    connection: sqlite3.Connection,
) -> Dict[str, object]:
    elapsed = _stage_elapsed_seconds(row["stage_entered_at"])
    payload = {
        "id": row["id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "phone": row["phone"],
        "created_at": row["created_at"],
        "current_stage": row["current_stage"],
        "stage_entered_at": row["stage_entered_at"],
        "time_in_current_stage_seconds": elapsed,
        "time_in_current_stage_days": elapsed / 86400,
    }
    if include_history:
        history = connection.execute(
            """SELECT id, actor_id, from_stage, to_stage, reason, created_at
               FROM candidate_stage_events WHERE candidate_id = ? ORDER BY id""",
            (row["id"],),
        ).fetchall()
        payload["history"] = [dict(event) for event in history]
    return payload


def list_candidates_grouped(database_path: Optional[Path] = None) -> Dict[str, List[Dict[str, object]]]:
    """List candidates grouped by current stage, with time in that stage."""
    grouped = {stage: [] for stage in (
        Stage.APPLIED.value,
        Stage.SCREENING.value,
        Stage.INTERVIEW.value,
        Stage.OFFER.value,
        Stage.HIRED.value,
        Stage.REJECTED.value,
    )}
    connection = connect(database_path)
    try:
        rows = connection.execute(
            """SELECT c.id, c.full_name, c.email, c.phone, c.created_at,
                      e.to_stage AS current_stage, e.created_at AS stage_entered_at
               FROM candidates AS c
               JOIN candidate_stage_events AS e ON e.id = (
                   SELECT id FROM candidate_stage_events
                   WHERE candidate_id = c.id ORDER BY id DESC LIMIT 1
               )
               ORDER BY c.full_name COLLATE NOCASE, c.id"""
        ).fetchall()
        for row in rows:
            grouped[row["current_stage"]].append(
                _candidate_payload(row, include_history=False, connection=connection)
            )
        return grouped
    finally:
        connection.close()


def get_candidate(candidate_id: int, database_path: Optional[Path] = None) -> Dict[str, object]:
    """Return one candidate, latest-stage duration, and complete audit history."""
    connection = connect(database_path)
    try:
        row = connection.execute(
            """SELECT c.id, c.full_name, c.email, c.phone, c.created_at,
                      e.to_stage AS current_stage, e.created_at AS stage_entered_at
               FROM candidates AS c
               JOIN candidate_stage_events AS e ON e.id = (
                   SELECT id FROM candidate_stage_events
                   WHERE candidate_id = c.id ORDER BY id DESC LIMIT 1
               )
               WHERE c.id = ?""",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise CandidateNotFound(f"Candidate {candidate_id} was not found.")
        return _candidate_payload(row, include_history=True, connection=connection)
    finally:
        connection.close()
