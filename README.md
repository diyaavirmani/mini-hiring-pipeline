# Mini Hiring Pipeline

A local-first hiring pipeline for one recruiter managing candidates for one job. The project includes a FastAPI API, recruiter interface, secure local configuration, SQLite database, and schema migration runner. Candidate stage rules, audit history, and read-only search are available through authenticated HTTP routes and the browser interface.

## Run locally

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
```

Edit `.env` and replace `APP_SECRET_KEY` with a random value. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then initialize the schema and start the API:

```bash
python -m hiring_pipeline.migrate
python -m hiring_pipeline.create_recruiter
python -m hiring_pipeline.seed
uvicorn hiring_pipeline.main:app --reload
```

Create one recruiter account with the CLI prompt, then open <http://127.0.0.1:8000> and sign in. The board groups candidates by stage, shows time in stage, and provides candidate creation, advance/reject actions, complete audit history, and a single search box. The API sets a one-hour, signed, HTTP-only recruiter cookie after login. It uses `SameSite=Lax`; production mode also marks the cookie Secure. The API docs remain at <http://127.0.0.1:8000/docs>. The local database is created at `data/hiring_pipeline.sqlite3`. `.env` and local SQLite files are ignored by Git.

`GEMINI_API_KEY` is optional. Without it, the deterministic search grammar handles supported queries and clearly explains unsupported ones. With a key, unsupported queries get one Gemini interpretation attempt, constrained to validated, read-only search filters. Set `GEMINI_MODEL` to override the default.

The fictional, readable fixture is [`data/sample_candidates.json`](data/sample_candidates.json). Seeding is safe to repeat: existing candidates with fixture emails and their histories are left unchanged. It creates examples for the typo target Priya Sharma; candidates currently in every stage; Priya and Karan in Screening for more than a week; Rahul and Fatima moved to Interview at the most recent Monday boundary in `APP_TIMEZONE`; four people who reached Offer without being hired, including Farah who was rejected after Offer; Vikram, who was hired; and three rejected candidates. Rejected candidates are excluded from the “everyone except rejected” group, while Hired candidates remain in it.

## Design choices

### Architecture

The app is a same-origin FastAPI service with a small browser client. The browser calls authenticated JSON routes; the API validates requests and delegates candidate operations to the pipeline service. That service applies the stage rules inside a serialized SQLite write transaction. The database stores candidate profiles and an append-only stage-event timeline; the current stage and time in stage come from the newest event. Search is read-only: deterministic parsing handles known filters, fuzzy name matching ranks likely candidates, and an optional AI provider can translate an otherwise unsupported query into a validated filter object for the same search service.

See the [architecture summary PDF](docs/architecture-summary.pdf) or its [Markdown source](docs/architecture-summary.md) for a compact component overview and repository link.

### Decisions and trade-offs

- **FastAPI with SQLite:** the project is for one recruiter and one job, so a single local database keeps setup and operation straightforward. SQLite transactions, foreign keys, and triggers give this small app useful integrity guarantees without a separate database service. WAL mode and a busy timeout support serialized concurrent writes for this local workload. The trade-off is that SQLite is a poor fit for horizontally scaled application instances or sustained multi-user write traffic; a server database would be a better fit as the product grows.
- **Append-only stage history:** current stage is derived from the latest stage event. Database triggers enforce the initial Applied event, one-step forward moves, rejection before Hired, terminal outcomes, and history immutability. The service validates transitions and reports stale moves clearly.
- **Versioned SQL migrations:** each numbered migration is applied once inside a write transaction and recorded in `schema_migrations`. Existing migration files should be treated as immutable; schema changes belong in a new migration.
- **Configuration from environment:** `.env.example` documents local settings. The app validates the secret length, timezone, and SQLite URL and gives a direct configuration error. Do not commit `.env` or use example secrets outside local development.
- **Protected recruiter actions:** a CLI creates one recruiter account with an Argon2 password hash. Login issues a signed, short-lived HTTP-only cookie; all candidate routes require that session.
- **Search rules first:** search parses known stage, duration, transition, outcome, and exclusion phrases deterministically. Name matching uses fuzzy token scores and returns the strongest name matches first. Supported filters combine with AND. A valid query may return zero matches; an unsupported query gets an explanation and examples.
- **Bounded AI fallback:** Gemini is only called when deterministic parsing cannot interpret the query. Its structured response is validated against the filter allowlist before the read-only search runs; it cannot generate SQL, change candidates, or move pipeline stages. This improves query flexibility but adds provider latency, availability, and credential requirements; searches supported by the deterministic parser work without AI credentials.

## Database schema

`migrations/0001_initial_schema.sql` creates:

- `recruiters`: unique email and password hash; plaintext passwords are not stored.
- `candidates`: candidate profile fields with case-insensitive unique email when supplied.
- `candidate_stage_events`: immutable, append-only stage history tied to a candidate and recruiter.
- `schema_migrations`: migration versions and application timestamps, created by the migration runner.

The stage-event insert trigger checks that the first event is Applied, later events start from the latest stage, forward moves advance by one step, rejection is allowed before Hired, and Hired/Rejected have no outgoing transitions. Foreign keys are enabled on every connection. Each move uses `BEGIN IMMEDIATE`; the service checks the caller's expected stage after obtaining the write lock, so a concurrent stale move returns a conflict without a second event.

Migration `0002_guard_initial_event.sql` corrects the initial trigger so a candidate's first Applied event is accepted while later forward moves remain constrained. Applied migrations are not edited in place; fixes are made in a new migration.

Migration `0003_single_recruiter.sql` adds a database trigger that prevents a second recruiter from being inserted, including concurrent account-creation attempts.

## Stage operations and tests

`create_candidate`, `advance_candidate`, `reject_candidate`, `get_candidate_stage`, and `get_candidate_history` are implemented in `src/hiring_pipeline/pipeline.py`. History is returned oldest-first and includes the actor, source and destination stages, reason, and UTC creation time. Rejection is valid from Applied, Screening, Interview, or Offer; no transition is valid after Hired or Rejected. Caller-provided expected stage protects against stale UI state, while the database trigger remains a second line of defense.

Run the full test suite with:

```bash
python -m unittest discover -s tests -v
```

All 37 tests pass. The suite covers initial stage and valid forward moves, rejection from every pre-Hired stage, terminal outcomes, database rejection of event updates/deletes/skipped stages, stale requests, concurrent advance/reject races, singleton recruiter integrity, complete history, login protection, request validation, safe unexpected-error responses, production cookie settings, configuration validation, search interpretation and ranking, combined filters, unsupported versus valid-zero-result queries, AI filter validation and provider failures, sample query coverage, Monday timestamps, stage distribution, and repeat-safe seeding.

## Test results and search evaluation

Fresh-checkout verification followed the commands above in an isolated clone on Python 3.9.6: dependency installation, migrations, recruiter creation, and seeding all succeeded. The full suite passed (37 tests), and search evaluation passed 19/19 queries: seven assignment examples, six combined or valid-zero-result searches, and six invalid-query cases. A live HTTP smoke check confirmed the board loads, anonymous candidate access is denied, recruiter login succeeds, all 12 fixtures are listed, and the typo query `sharam` ranks Priya Sharma first. Browser checks cover sign-in, the six stage columns, candidate creation, stage advancement, rejection and its audit reason, complete timeline display, typo search, valid zero-result and unsupported-query feedback, loading/error/retry states, and post-move result refresh.

The authenticated endpoints are listed in `/docs`: auth routes, candidate create/list/detail/advance/reject, and `POST /api/search` with a JSON `q` field. Search text stays out of URL paths and query strings. Search returns `count`, ranked `results`, `interpretation_source`, and a plain-language explanation. A valid query with no matches returns `200` with an empty results list; a query the rules and configured fallback cannot interpret returns `422` with suggestions. Candidate detail includes complete audit history; list and search results include current stage duration. Expected names and unsupported-query cases are in [`data/search_evaluation.json`](data/search_evaluation.json).

Run the actual search examples and compare them to expected names with:

```bash
python -m hiring_pipeline.evaluate_search
```

The runner uses a fresh temporary database and fictional seed data, so it does not change the local app database.

## What I would improve with more time

With more time, add login-attempt throttling, protected/encrypted backup handling for candidate data, and automated browser regression tests. The local SQLite file is not encrypted at rest, so host and backup permissions still matter. AWS and Docker are out of scope.
