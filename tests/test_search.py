"""Deterministic search, ranking, zero-result, and AI-fallback tests."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hiring_pipeline.database import apply_migrations, connect
from hiring_pipeline.pipeline import create_candidate
from hiring_pipeline.search import (
    QueryNotUnderstood,
    SearchFilters,
    SearchProviderUnavailable,
    search_candidates,
    validate_ai_filters,
)
from hiring_pipeline.seed import DEFAULT_FIXTURE, seed_sample_data


class CandidateSearchTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "search.sqlite3"
        apply_migrations(self.database_path)
        connection = connect(self.database_path)
        cursor = connection.execute(
            "INSERT INTO recruiters(email, password_hash) VALUES (?, ?)",
            ("recruiter@example.test", "test-hash"),
        )
        self.actor_id = cursor.lastrowid
        connection.close()
        self.now = datetime.now(timezone.utc)
        seed_sample_data(
            self.database_path,
            fixture_path=DEFAULT_FIXTURE,
            now=self.now,
            timezone_name="Asia/Kolkata",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_all_evaluation_queries_return_expected_best_matches(self):
        from hiring_pipeline.evaluate_search import EVALUATION_FILE
        import json

        cases = json.loads(EVALUATION_FILE.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(query=case["query"]):
                result = search_candidates(
                    case["query"],
                    self.database_path,
                    timezone_name="Asia/Kolkata",
                    now=self.now,
                )
                self.assertEqual(
                    [candidate["full_name"] for candidate in result["results"]],
                    case["expected_names"],
                )

    def test_similar_names_rank_exact_match_ahead_of_fuzzy_match(self):
        create_candidate(
            self.actor_id,
            "Priya Sharme",
            database_path=self.database_path,
            email="priya.sharme@example.test",
        )
        result = search_candidates(
            "Find Priya Sharma", self.database_path, now=self.now
        )
        self.assertEqual(
            [candidate["full_name"] for candidate in result["results"]],
            ["Priya Sharma", "Priya Sharme"],
        )
        self.assertGreater(result["results"][0]["match_score"], result["results"][1]["match_score"])

    def test_combined_filters_intersect_name_stage_duration_and_exclusion(self):
        result = search_candidates(
            "Priya in Screening for more than a week except rejected",
            self.database_path,
            timezone_name="Asia/Kolkata",
            now=self.now,
        )
        self.assertEqual([candidate["full_name"] for candidate in result["results"]], ["Priya Sharma"])

    def test_recognized_name_search_can_legitimately_return_zero(self):
        result = search_candidates(
            "Find No Such Candidate", self.database_path, now=self.now
        )
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["results"], [])
        self.assertIn("Understood as", result["explanation"])

    def test_unsupported_query_is_explained(self):
        with self.assertRaises(QueryNotUnderstood) as raised:
            search_candidates("Tell me a joke", self.database_path, now=self.now)
        self.assertIn("couldn't understand", str(raised.exception))
        self.assertTrue(raised.exception.examples)

        with self.assertRaises(QueryNotUnderstood):
            search_candidates("Who is in Interview banana?", self.database_path, now=self.now)

    def test_ai_fallback_only_accepts_validated_read_filters(self):
        def interpreted(query):
            return SearchFilters(current_stage="Applied", source="ai")

        result = search_candidates(
            "Who is newly available?",
            self.database_path,
            now=self.now,
            ai_interpreter=interpreted,
        )
        self.assertEqual(result["interpretation_source"], "ai")
        self.assertEqual([candidate["full_name"] for candidate in result["results"]], ["Nisha Kapoor"])

        malformed = {
            "understood": True,
            "name_query": "",
            "current_stage": "DELETE",
            "excluded_stages": [],
            "minimum_days_in_stage": 0,
            "moved_to": "",
            "moved_since_monday": False,
            "reached_stage": "",
            "not_hired": False,
        }
        with self.assertRaises(ValueError):
            validate_ai_filters(malformed)
        malformed["current_stage"] = ""
        malformed["minimum_days_in_stage"] = float("nan")
        malformed["moved_to"] = "Screening"
        with self.assertRaises(ValueError):
            validate_ai_filters(malformed)

    def test_ai_transport_failure_has_a_clear_fallback_error(self):
        def failed(query):
            raise TimeoutError("private transport detail")

        with self.assertRaises(SearchProviderUnavailable) as raised:
            search_candidates(
                "Who is newly available?",
                self.database_path,
                ai_interpreter=failed,
            )
        self.assertIn("temporarily unavailable", str(raised.exception))
        self.assertNotIn("private transport detail", str(raised.exception))

    def test_gemini_uses_structured_filter_response_and_validates_it(self):
        from hiring_pipeline.search import GeminiQueryInterpreter

        payload = {
            "understood": True,
            "name_query": "",
            "current_stage": "Interview",
            "excluded_stages": [],
            "minimum_days_in_stage": 0,
            "moved_to": "",
            "moved_since_monday": False,
            "reached_stage": "",
            "not_hired": False,
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({
                    "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]
                }).encode("utf-8")

        interpreter = GeminiQueryInterpreter("test-api-key", model="gemini-test")
        with patch("hiring_pipeline.search.urlopen", return_value=FakeResponse()) as mocked:
            filters = interpreter("Who is in Interview?")
        self.assertEqual(filters.current_stage, "Interview")
        self.assertEqual(filters.source, "ai")
        request = mocked.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            request_body["generationConfig"]["responseFormat"]["text"]["mimeType"],
            "application/json",
        )
        schema = request_body["generationConfig"]["responseFormat"]["text"]["schema"]
        self.assertEqual(schema["properties"]["moved_since_monday"]["type"], "boolean")
        self.assertEqual(schema["properties"]["not_hired"]["type"], "boolean")
        self.assertEqual(request.get_header("X-goog-api-key"), "test-api-key")


if __name__ == "__main__":
    unittest.main()
