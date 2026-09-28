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
