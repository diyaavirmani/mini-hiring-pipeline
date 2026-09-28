# Mini Hiring Pipeline — Architecture Summary

Repository: https://github.com/diyaavirmani/mini-hiring-pipeline

## Scope

One recruiter manages candidates for one job through Applied → Screening → Interview → Offer → Hired. Rejection is allowed before Hired. The browser board supports candidate creation, stage actions, current-stage duration, complete event history, and one search box.

## Request and data flow

1. The same-origin browser client sends JSON requests to FastAPI. Recruiter-only routes require the signed, short-lived HTTP-only session cookie.
2. FastAPI validates request schemas, handles authentication, and calls the pipeline or search service. Errors use explicit client responses; unexpected server errors do not expose internals.
3. The pipeline service performs moves in a SQLite `BEGIN IMMEDIATE` transaction and checks the caller's expected current stage after acquiring the write lock. A concurrent stale request gets a conflict and cannot append a second move.
4. SQLite stores candidate profiles and immutable stage events. Foreign keys and triggers enforce event integrity, legal one-step transitions, final outcomes, and one recruiter. Current stage and stage duration are derived from the newest event.
5. Search parses supported filters deterministically, combines them, fuzzy-ranks name matches, and distinguishes unsupported queries from valid searches with no matches. Only when deterministic parsing cannot understand a query may Gemini return a structured filter proposal. The proposal is allowlist-validated, then passed to the existing read-only search service; AI cannot write SQL or mutate candidate data.

## Main trade-offs

SQLite keeps setup simple and supports this one-recruiter workload, but is not intended for horizontally scaled instances or sustained concurrent write traffic. A same-origin interface avoids a separate frontend deployment and cross-origin session setup. The AI fallback makes natural-language searches more flexible but depends on provider availability and adds latency; deterministic searches need no AI credential. The local database is not encrypted at rest.

## Verification snapshot

The documented test suite covers transition and audit integrity, concurrent moves, API validation and access control, configuration, search parsing/ranking, AI failure and output validation, and repeat-safe sample seeding. Search evaluation covers all assignment examples plus combined, valid-empty, and unsupported queries. See the repository README for the latest run results and local setup instructions.

## Out of scope

AWS and Docker deployment are out of scope for this iteration.
