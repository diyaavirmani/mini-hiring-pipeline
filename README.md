# Mini Hiring Pipeline

A local-first hiring pipeline for one recruiter managing candidates for one job. The project includes a FastAPI scaffold, secure local configuration, SQLite database, and schema migration runner. Candidate stage rules and audit-history operations live in `hiring_pipeline.pipeline`; HTTP routes, authentication flows, the browser UI, and search will be added in later steps.

## Run locally

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
```

Edit `.env` and replace `APP_SECRET_KEY` with a random value. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then initialize the schema and start the API:

```bash
python -m hiring_pipeline.migrate
uvicorn hiring_pipeline.main:app --reload
```

Open <http://127.0.0.1:8000/docs> for the API scaffold or <http://127.0.0.1:8000/healthz> for its health response. The local database is created at `data/hiring_pipeline.sqlite3`. `.env` and local SQLite files are ignored by Git.

## Design choices

- **FastAPI with SQLite:** the project is for one recruiter and one job, so a single local database keeps setup and operation straightforward. SQLite transactions, foreign keys, and triggers give this small app useful integrity guarantees without a separate database service. WAL mode and a busy timeout support serialized concurrent writes for this local workload. A server database would be a better fit if the app grew to multiple concurrent users or deployments.
- **Append-only stage history:** current stage is derived from the latest stage event. Database triggers enforce the initial Applied event, one-step forward moves, rejection before Hired, terminal outcomes, and history immutability. The app service will also validate transitions and report stale moves clearly.
- **Versioned SQL migrations:** each numbered migration is applied once inside a write transaction and recorded in `schema_migrations`. Existing migration files should be treated as immutable; schema changes belong in a new migration.
- **Configuration from environment:** `.env.example` documents local settings. The app validates the secret length, timezone, and SQLite URL and gives a direct configuration error. Do not commit `.env` or use example secrets outside local development.
- **Thin first step:** this commit adds the candidate domain service and tests but still exposes only health and API docs. Recruiter login, protected candidate/transition endpoints, the UI, and search remain for later steps.

## Database schema

`migrations/0001_initial_schema.sql` creates:

- `recruiters`: unique email and password hash; plaintext passwords are not stored.
- `candidates`: candidate profile fields with case-insensitive unique email when supplied.
- `candidate_stage_events`: immutable, append-only stage history tied to a candidate and recruiter.
- `schema_migrations`: migration versions and application timestamps, created by the migration runner.

The stage-event insert trigger checks that the first event is Applied, later events start from the latest stage, forward moves advance by one step, rejection is allowed before Hired, and Hired/Rejected have no outgoing transitions. Foreign keys are enabled on every connection. Each move uses `BEGIN IMMEDIATE`; the service checks the caller's expected stage after obtaining the write lock, so a concurrent stale move returns a conflict without a second event.

Migration `0002_guard_initial_event.sql` corrects the initial trigger so a candidate's first Applied event is accepted while later forward moves remain constrained. Applied migrations are not edited in place; fixes are made in a new migration.

## Stage operations and tests

`create_candidate`, `advance_candidate`, `reject_candidate`, `get_candidate_stage`, and `get_candidate_history` are implemented in `src/hiring_pipeline/pipeline.py`. History is returned oldest-first and includes the actor, source and destination stages, reason, and UTC creation time. Rejection is valid from Applied, Screening, Interview, or Offer; no transition is valid after Hired or Rejected. Caller-provided expected stage protects against stale UI state, while the database trigger remains a second line of defense.

Run the focused tests with:

```bash
python -m unittest discover -s tests -v
```

The test suite covers initial stage and valid forward moves, rejection from every pre-Hired stage, terminal outcomes, database rejection of event updates/deletes/skipped stages, stale requests, complete history, missing candidates, and two concurrent requests racing from the same stage. The seven tests pass in this commit.

## Checks and current status

The stage-domain test suite passes (7 tests). The HTTP routes, authentication, browser UI, and search are not yet implemented and do not yet have behavior tests.

## Planned next steps

Add recruiter authentication and protected HTTP actions, connect the existing candidate service to the UI, calculate stage duration, then add deterministic search with validated AI interpretation fallback and a search evaluation set. Add fictional sample candidates and tests with those features. AWS and Docker are out of scope.
