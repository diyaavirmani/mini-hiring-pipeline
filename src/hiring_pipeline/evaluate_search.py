"""Run the documented search evaluation against a temporary seeded database."""

import json
from pathlib import Path
import tempfile

from .database import apply_migrations, connect
from .search import search_candidates
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
            })
        return reports


def main():
    reports = run_evaluation()
    passed = sum(1 for report in reports if report["passed"])
    for report in reports:
        result = ", ".join(report["actual_names"]) or "(no matches)"
        marker = "PASS" if report["passed"] else "FAIL"
        print("[{}] {} -> {}".format(marker, report["query"], result))
    print("{}/{} search examples passed".format(passed, len(reports)))
    if passed != len(reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
