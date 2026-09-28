"""Coverage and idempotency tests for fictional sample pipeline data."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from hiring_pipeline.database import apply_migrations, connect
from hiring_pipeline.pipeline import get_candidate, list_candidates_grouped
from hiring_pipeline.seed import DEFAULT_FIXTURE, seed_sample_data


class SampleSeedTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "seed.sqlite3"
        apply_migrations(self.database_path)
        connection = connect(self.database_path)
        connection.execute(
            "INSERT INTO recruiters(email, password_hash) VALUES (?, ?)",
            ("recruiter@example.test", "test-hash"),
        )
        connection.close()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.result = seed_sample_data(
            self.database_path,
            fixture_path=DEFAULT_FIXTURE,
            now=self.now,
            timezone_name="Asia/Kolkata",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fixture_is_readable_and_covers_every_pipeline_outcome(self):
        self.assertTrue(DEFAULT_FIXTURE.is_file())
        self.assertEqual(self.result, {"created": 12, "skipped": 0})
        groups = list_candidates_grouped(self.database_path)
        self.assertEqual({stage: len(candidates) for stage, candidates in groups.items()}, {
            "Applied": 1,
            "Screening": 2,
            "Interview": 2,
            "Offer": 3,
            "Hired": 1,
            "Rejected": 3,
        })
        self.assertIn("Priya Sharma", [item["full_name"] for item in groups["Screening"]])
        self.assertEqual([item["full_name"] for item in groups["Hired"]], ["Vikram Nair"])

    def test_screening_examples_are_over_a_week_old_and_name_typo_has_target(self):
        groups = list_candidates_grouped(self.database_path)
        screening = {item["full_name"]: item for item in groups["Screening"]}
        self.assertGreater(screening["Priya Sharma"]["time_in_current_stage_days"], 7)
        self.assertGreater(screening["Karan Malhotra"]["time_in_current_stage_days"], 7)
        # The assignment's fuzzy query "sharam" should rank this fictional target.
        self.assertEqual(screening["Priya Sharma"]["email"], "priya.sharma@example.test")

    def test_interview_moves_are_on_most_recent_monday_in_app_timezone(self):
        groups = list_candidates_grouped(self.database_path)
        moved = {item["full_name"]: item for item in groups["Interview"]}
        local_now = self.now.astimezone(ZoneInfo("Asia/Kolkata"))
        expected_monday = local_now.date() - timedelta(days=local_now.weekday())
        for name in ("Rahul Mehta", "Fatima Sheikh"):
            candidate = moved[name]
            history = get_candidate(candidate["id"], self.database_path)["history"]
            entered_at = datetime.fromisoformat(history[-1]["created_at"].replace("Z", "+00:00"))
            self.assertEqual(entered_at.astimezone(ZoneInfo("Asia/Kolkata")).date(), expected_monday)
            self.assertEqual(history[-1]["to_stage"], "Interview")

    def test_offer_without_hired_and_excluding_rejected_examples(self):
        connection = connect(self.database_path)
        try:
            reached_offer = {
                row["full_name"]
                for row in connection.execute(
                    """SELECT DISTINCT c.full_name FROM candidates c
                       JOIN candidate_stage_events e ON e.candidate_id = c.id
                       WHERE e.to_stage = 'Offer'
                         AND NOT EXISTS (
                             SELECT 1 FROM candidate_stage_events hired
                             WHERE hired.candidate_id = c.id AND hired.to_stage = 'Hired'
                         )"""
                )
            }
        finally:
            connection.close()
        self.assertEqual(
            reached_offer,
            {"Anita Desai", "Meera Iyer", "Joseph Fernandes", "Farah Qureshi"},
        )

        groups = list_candidates_grouped(self.database_path)
        active = [candidate for stage, members in groups.items() if stage != "Rejected" for candidate in members]
        self.assertEqual(len(active), 9)
        self.assertTrue(any(candidate["current_stage"] == "Hired" for candidate in active))
        self.assertEqual({candidate["full_name"] for candidate in groups["Rejected"]}, {
            "Farah Qureshi", "Aisha Khan", "Dev Patel"
        })

    def test_rerunning_seed_skips_existing_records_without_changing_history(self):
        connection = connect(self.database_path)
        try:
            before = connection.execute(
                "SELECT COUNT(*) AS total FROM candidate_stage_events"
            ).fetchone()["total"]
        finally:
            connection.close()

        rerun = seed_sample_data(
            self.database_path,
            fixture_path=DEFAULT_FIXTURE,
            now=self.now,
            timezone_name="Asia/Kolkata",
        )
        self.assertEqual(rerun, {"created": 0, "skipped": 12})
        connection = connect(self.database_path)
        try:
            after = connection.execute(
                "SELECT COUNT(*) AS total FROM candidate_stage_events"
            ).fetchone()["total"]
        finally:
            connection.close()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
