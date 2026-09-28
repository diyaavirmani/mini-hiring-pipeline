# Mini Hiring Pipeline

A local-first hiring pipeline for one recruiter managing candidates for one job. This commit sets up the FastAPI application, secure local configuration, SQLite database, and schema migration runner. Candidate management, authentication flows, audit-history screens, and search will be added in later steps.

## Run locally

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
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
- **Thin first step:** this setup exposes health and API docs only. It does not yet implement recruiter login, candidate CRUD, transition endpoints, UI, or search.

## Database schema

`migrations/0001_initial_schema.sql` creates:

- `recruiters`: unique email and password hash; plaintext passwords are not stored.
- `candidates`: candidate profile fields with case-insensitive unique email when supplied.
- `candidate_stage_events`: immutable, append-only stage history tied to a candidate and recruiter.
- `schema_migrations`: migration versions and application timestamps, created by the migration runner.

The stage-event insert trigger checks that the first event is Applied, later events start from the latest stage, forward moves advance by one step, and Hired/Rejected have no outgoing transitions. Foreign keys are enabled on every connection. Each move will use an immediate write transaction so concurrent requests cannot advance from stale state unnoticed.

## Checks and current status

This setup commit does not yet add application behavior tests. The migration runner and API will receive automated tests as the candidate and search features are implemented; no test results are claimed at this scaffold stage.

## Planned next steps

Add recruiter authentication and protected actions, candidate creation and stage movement, complete audit history and stage duration, then deterministic search with validated AI interpretation fallback and a search evaluation set. Add fictional sample candidates and tests with those features. AWS and Docker are out of scope.
