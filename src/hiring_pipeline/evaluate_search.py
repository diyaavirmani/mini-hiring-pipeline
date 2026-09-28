"""Run the documented search evaluation against a temporary seeded database."""

import json
from pathlib import Path
import tempfile

from .database import apply_migrations, connect
from .search import QueryNotUnderstood, search_candidates
from .seed import DEFAULT_FIXTURE, seed_sample_data


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_FILE = PROJECT_ROOT / "data" / "search_evaluation.json"


def run_evaluation():
    evaluation = json.loads(EVALUATION_FILE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="hiring-search-eval-") as directory:
        database_path = Path(directory) / "evaluation.sqlite3"
        apply_migrations(database_path)
        connection = connect(database_path)
        connection.execute(
            "INSERT INTO recruiters(email, password_hash) VALUES (?, ?)",
            ("evaluation@example.test", "temporary-evaluation-hash"),
        )
        connection.close()
        seed_sample_data(database_path, fixture_path=DEFAULT_FIXTURE)

        reports = []
        for case in evaluation["cases"]:
            result = search_candidates(
                case["query"],
                database_path,
                timezone_name="Asia/Kolkata",
            )
            actual_names = [item["full_name"] for item in result["results"]]
            reports.append({
                "query": case["query"],
                "expected_names": case["expected_names"],
                "actual_names": actual_names,
                "passed": actual_names == case["expected_names"],
                "kind": "valid",
            })
        for query in evaluation.get("invalid_queries", []):
            try:
                result = search_candidates(
                    query,
                    database_path,
                    timezone_name="Asia/Kolkata",
                )
            except QueryNotUnderstood as exc:
                reports.append({
                    "query": query,
                    "actual_names": [],
                    "explanation": str(exc),
                    "passed": True,
                    "kind": "invalid",
                })
            else:
                reports.append({
                    "query": query,
                    "actual_names": [item["full_name"] for item in result["results"]],
                    "explanation": result["explanation"],
                    "passed": False,
                    "kind": "invalid",
                })
        return reports


def main():
    reports = run_evaluation()
    passed = sum(1 for report in reports if report["passed"])
    for report in reports:
        result = ", ".join(report["actual_names"]) or "(no matches)"
        kind = "INVALID" if report["kind"] == "invalid" else "SEARCH"
        marker = "PASS" if report["passed"] else "FAIL"
        details = " — " + report["explanation"] if report.get("explanation") else ""
        print("[{} {}] {} -> {}{}".format(marker, kind, report["query"], result, details))
    print("{}/{} search and invalid-query evaluations passed".format(passed, len(reports)))
    if passed != len(reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
