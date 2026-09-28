"""Tests for transition rules, append-only history, and concurrent moves."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from hiring_pipeline.database import apply_migrations, connect
from hiring_pipeline.pipeline import (
    FINAL_STAGES,
    CandidateNotFound,
    FinalStageError,
    StageConflict,
    advance_candidate,
    create_candidate,
    get_candidate_history,
    get_candidate_stage,
    reject_candidate,
)


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        apply_migrations(self.database_path)
        connection = connect(self.database_path)
        cursor = connection.execute(
            "INSERT INTO recruiters(email, password_hash) VALUES (?, ?)",
            ("recruiter@example.test", "test-hash"),
        )
        self.actor_id = cursor.lastrowid
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def candidate(self, name="Priya Sharma"):
        return create_candidate(
            self.actor_id, name, database_path=self.database_path
        )["id"]

    def move_to(self, candidate_id, target_stage):
        for expected, destination in (
            ("Applied", "Screening"),
            ("Screening", "Interview"),
            ("Interview", "Offer"),
            ("Offer", "Hired"),
        ):
            if destination == target_stage:
                return advance_candidate(
                    candidate_id,
                    self.actor_id,
                    expected,
                    database_path=self.database_path,
                )
            if expected == target_stage:
                return
            if get_candidate_stage(candidate_id, self.database_path) == expected:
                advance_candidate(
                    candidate_id,
                    self.actor_id,
                    expected,
                    database_path=self.database_path,
                )
        self.assertEqual(get_candidate_stage(candidate_id, self.database_path), target_stage)

    def test_candidate_starts_in_applied_and_forward_moves_are_one_step(self):
        candidate_id = self.candidate()
        self.assertEqual(get_candidate_stage(candidate_id, self.database_path), "Applied")

        expected_moves = [
            ("Applied", "Screening"),
            ("Screening", "Interview"),
            ("Interview", "Offer"),
            ("Offer", "Hired"),
        ]
        for current, next_stage in expected_moves:
            result = advance_candidate(
                candidate_id,
                self.actor_id,
                current,
                database_path=self.database_path,
            )
            self.assertEqual((result["from_stage"], result["to_stage"]), (current, next_stage))

        history = get_candidate_history(candidate_id, self.database_path)
        self.assertEqual([event["to_stage"] for event in history], [
            "Applied", "Screening", "Interview", "Offer", "Hired"
        ])
        self.assertEqual(len(history), 5)

    def test_stale_stage_is_rejected_without_writing_history(self):
        candidate_id = self.candidate()
        with self.assertRaises(StageConflict):
            advance_candidate(
                candidate_id,
                self.actor_id,
                "Screening",
                database_path=self.database_path,
            )
        self.assertEqual(len(get_candidate_history(candidate_id, self.database_path)), 1)

    def test_rejection_is_allowed_from_every_pre_hire_stage(self):
        for stage in ("Applied", "Screening", "Interview", "Offer"):
            with self.subTest(stage=stage):
                candidate_id = self.candidate("Candidate " + stage)
                if stage != "Applied":
                    self.move_to(candidate_id, stage)
                reject_candidate(
                    candidate_id,
                    self.actor_id,
                    stage,
                    reason="Role requirements not met",
                    database_path=self.database_path,
                )
                history = get_candidate_history(candidate_id, self.database_path)
                self.assertEqual(history[-1]["to_stage"], "Rejected")
                self.assertEqual(history[-1]["reason"], "Role requirements not met")

    def test_hired_and_rejected_are_final(self):
        candidate_id = self.candidate()
        self.move_to(candidate_id, "Hired")
        self.assertEqual(get_candidate_stage(candidate_id, self.database_path), "Hired")
        with self.assertRaises(FinalStageError):
            advance_candidate(
                candidate_id, self.actor_id, "Hired", database_path=self.database_path
            )
        with self.assertRaises(FinalStageError):
            reject_candidate(
                candidate_id, self.actor_id, "Hired", database_path=self.database_path
            )

        rejected_id = self.candidate("Rejected Candidate")
        reject_candidate(
            rejected_id, self.actor_id, "Applied", database_path=self.database_path
        )
        with self.assertRaises(FinalStageError):
            advance_candidate(
                rejected_id,
                self.actor_id,
                "Rejected",
                database_path=self.database_path,
            )
        with self.assertRaises(FinalStageError):
            reject_candidate(
                rejected_id,
                self.actor_id,
                "Rejected",
                database_path=self.database_path,
            )
        self.assertEqual(FINAL_STAGES, {"Hired", "Rejected"})

    def test_database_rejects_event_updates_and_deletes(self):
        candidate_id = self.candidate()
        connection = connect(self.database_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE candidate_stage_events SET reason = 'edited' WHERE candidate_id = ?",
                    (candidate_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM candidate_stage_events WHERE candidate_id = ?",
                    (candidate_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO candidate_stage_events
                       (candidate_id, actor_id, from_stage, to_stage)
                       VALUES (?, ?, 'Applied', 'Offer')""",
                    (candidate_id, self.actor_id),
                )
        finally:
            connection.close()
        self.assertEqual(len(get_candidate_history(candidate_id, self.database_path)), 1)

    def test_concurrent_moves_from_same_stage_allow_only_one(self):
        candidate_id = self.candidate()
        barrier = threading.Barrier(2)

        def attempt_move():
            barrier.wait(timeout=3)
            return advance_candidate(
                candidate_id,
                self.actor_id,
                "Applied",
                database_path=self.database_path,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt_move) for _ in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=10))
                except StageConflict as exc:
                    outcomes.append(exc)

        self.assertEqual(sum(isinstance(outcome, dict) for outcome in outcomes), 1)
        self.assertEqual(sum(isinstance(outcome, StageConflict) for outcome in outcomes), 1)
        self.assertEqual(get_candidate_stage(candidate_id, self.database_path), "Screening")
        self.assertEqual(len(get_candidate_history(candidate_id, self.database_path)), 2)

    def test_history_for_unknown_candidate_is_clear(self):
        with self.assertRaises(CandidateNotFound):
            get_candidate_history(999, self.database_path)


if __name__ == "__main__":
    unittest.main()
