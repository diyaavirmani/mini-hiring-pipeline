"""HTTP integration tests for recruiter auth and candidate endpoints."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("APP_SECRET_KEY", "test-only-secret-key-value-not-for-use")

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from hiring_pipeline.database import apply_migrations, connect
from hiring_pipeline.main import create_app
from hiring_pipeline.search import SearchFilters
from hiring_pipeline.seed import DEFAULT_FIXTURE, seed_sample_data


class CandidateApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "api.sqlite3"
        apply_migrations(self.database_path)
        connection = connect(self.database_path)
        cursor = connection.execute(
            "INSERT INTO recruiters(email, password_hash) VALUES (?, ?)",
            ("recruiter@example.test", PasswordHasher().hash("correct-horse-battery")),
        )
        self.recruiter_id = cursor.lastrowid
        connection.close()
        self.client = TestClient(
            create_app(
                database_path=self.database_path,
                secret_key="integration-test-secret-key-32-chars",
                secure_cookie=False,
            )
        )

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def login(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email": "recruiter@example.test", "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        return response

    def create_candidate(self, **overrides):
        body = {"full_name": "Priya Sharma", "email": "priya@example.test"}
        body.update(overrides)
        return self.client.post("/api/candidates", json=body)

    def test_authentication_protects_candidate_actions(self):
        response = self.client.get("/api/candidates")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")

        rejected_login = self.client.post(
            "/api/auth/login",
            json={"email": "recruiter@example.test", "password": "incorrect"},
        )
        self.assertEqual(rejected_login.status_code, 401)
        self.assertEqual(rejected_login.json()["error"]["code"], "invalid_credentials")

        self.login()
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["recruiter"]["id"], self.recruiter_id)
        self.assertEqual(self.create_candidate().status_code, 201)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_create_list_open_advance_reject_and_history(self):
        self.login()
        created = self.create_candidate(full_name="  Priya Sharma  ")
        self.assertEqual(created.status_code, 201, created.text)
        candidate = created.json()
        candidate_id = candidate["id"]
        self.assertEqual(candidate["current_stage"], "Applied")
        self.assertEqual([event["to_stage"] for event in candidate["history"]], ["Applied"])
        self.assertEqual(candidate["stage_entered_at"], candidate["history"][-1]["created_at"])
        self.assertGreaterEqual(candidate["time_in_current_stage_seconds"], 0)
        self.assertGreaterEqual(candidate["time_in_current_stage_days"], 0)

        listing = self.client.get("/api/candidates")
        self.assertEqual(listing.status_code, 200)
        groups = {group["stage"]: group["candidates"] for group in listing.json()["groups"]}
        self.assertEqual([item["id"] for item in groups["Applied"]], [candidate_id])
        self.assertEqual(groups["Screening"], [])

        opened = self.client.get(f"/api/candidates/{candidate_id}")
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json()["history"], candidate["history"])

        advanced = self.client.post(
            f"/api/candidates/{candidate_id}/advance", json={"expected_stage": "Applied"}
        )
        self.assertEqual(advanced.status_code, 200, advanced.text)
        self.assertEqual(advanced.json()["current_stage"], "Screening")
        self.assertEqual(
            advanced.json()["stage_entered_at"], advanced.json()["history"][-1]["created_at"]
        )
        self.assertEqual(
            [event["to_stage"] for event in advanced.json()["history"]],
            ["Applied", "Screening"],
        )
        self.assertGreaterEqual(advanced.json()["time_in_current_stage_seconds"], 0)

        rejected = self.client.post(
            f"/api/candidates/{candidate_id}/reject",
            json={"expected_stage": "Screening", "reason": "Role requirements not met"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["current_stage"], "Rejected")
        self.assertEqual(rejected.json()["history"][-1]["reason"], "Role requirements not met")
        self.assertEqual(len(rejected.json()["history"]), 3)

        final_move = self.client.post(
            f"/api/candidates/{candidate_id}/advance", json={"expected_stage": "Rejected"}
        )
        self.assertEqual(final_move.status_code, 409)
        self.assertEqual(final_move.json()["error"]["code"], "transition_conflict")

    def test_invalid_input_duplicate_email_and_missing_candidate_errors(self):
        self.login()
        invalid_name = self.create_candidate(full_name="   ")
        self.assertEqual(invalid_name.status_code, 422)
        self.assertEqual(invalid_name.json()["error"]["code"], "invalid_request")

        invalid_email = self.create_candidate(email="not-an-email")
        self.assertEqual(invalid_email.status_code, 422)
        self.assertEqual(invalid_email.json()["error"]["details"][0]["field"], "email")

        created = self.create_candidate()
        self.assertEqual(created.status_code, 201)
        duplicate = self.create_candidate(full_name="Another Candidate")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["error"]["code"], "candidate_conflict")

        missing = self.client.get("/api/candidates/987654")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "candidate_not_found")

    def test_stale_move_returns_conflict_and_does_not_add_history(self):
        self.login()
        created = self.create_candidate()
        candidate_id = created.json()["id"]
        first = self.client.post(
            f"/api/candidates/{candidate_id}/advance", json={"expected_stage": "Applied"}
        )
        self.assertEqual(first.status_code, 200)
        stale = self.client.post(
            f"/api/candidates/{candidate_id}/advance", json={"expected_stage": "Applied"}
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "transition_conflict")
        current = self.client.get(f"/api/candidates/{candidate_id}").json()
        self.assertEqual(len(current["history"]), 2)

    def test_search_api_distinguishes_zero_results_from_unrecognized_queries(self):
        self.login()
        seed_sample_data(self.database_path, fixture_path=DEFAULT_FIXTURE)

        typo = self.client.get("/api/search", params={"q": "sharam"})
        self.assertEqual(typo.status_code, 200, typo.text)
        self.assertEqual([item["full_name"] for item in typo.json()["results"]], ["Priya Sharma"])

        combined = self.client.get(
            "/api/search",
            params={"q": "Priya in Screening for more than a week except rejected"},
        )
        self.assertEqual(combined.status_code, 200, combined.text)
        self.assertEqual([item["full_name"] for item in combined.json()["results"]], ["Priya Sharma"])

        zero = self.client.get("/api/search", params={"q": "Find No Such Candidate"})
        self.assertEqual(zero.status_code, 200)
        self.assertEqual(zero.json()["count"], 0)

        unclear = self.client.get("/api/search", params={"q": "Tell me a joke"})
        self.assertEqual(unclear.status_code, 422)
        self.assertEqual(unclear.json()["error"]["code"], "query_not_understood")
        self.assertTrue(unclear.json()["error"]["examples"])

    def test_search_api_can_use_filter_only_ai_fallback(self):
        seed_sample_data(self.database_path, fixture_path=DEFAULT_FIXTURE)
        app = create_app(
            database_path=self.database_path,
            secret_key="integration-test-secret-key-32-chars",
            secure_cookie=False,
            ai_interpreter=lambda query: SearchFilters(current_stage="Applied", source="ai"),
        )
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": "recruiter@example.test", "password": "correct-horse-battery"},
            )
            self.assertEqual(login.status_code, 200)
            response = client.get("/api/search", params={"q": "Who is newly available?"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["interpretation_source"], "ai")
            self.assertEqual(
                [item["full_name"] for item in response.json()["results"]], ["Nisha Kapoor"]
            )


if __name__ == "__main__":
    unittest.main()
