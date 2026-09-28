CREATE TABLE recruiters (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE
        CHECK (length(trim(email)) > 0),
    password_hash TEXT NOT NULL
        CHECK (length(password_hash) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE candidates (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL CHECK (length(trim(full_name)) > 0),
    email TEXT COLLATE NOCASE UNIQUE,
    phone TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE candidate_stage_events (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE RESTRICT,
    actor_id INTEGER NOT NULL REFERENCES recruiters(id) ON DELETE RESTRICT,
    from_stage TEXT CHECK (
        from_stage IS NULL OR from_stage IN
        ('Applied', 'Screening', 'Interview', 'Offer', 'Hired', 'Rejected')
    ),
    to_stage TEXT NOT NULL CHECK (
        to_stage IN ('Applied', 'Screening', 'Interview', 'Offer', 'Hired', 'Rejected')
    ),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (from_stage IS NULL AND to_stage = 'Applied') OR
        (from_stage IS NOT NULL AND from_stage <> to_stage)
    )
);

CREATE INDEX candidate_stage_events_candidate_id_id
    ON candidate_stage_events(candidate_id, id DESC);

CREATE TRIGGER candidate_stage_events_validate_insert
BEFORE INSERT ON candidate_stage_events
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM candidate_stage_events
            WHERE candidate_id = NEW.candidate_id
        ) AND NOT (NEW.from_stage IS NULL AND NEW.to_stage = 'Applied')
        THEN RAISE(ABORT, 'candidate must start in Applied')
    END;

    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM candidate_stage_events
            WHERE candidate_id = NEW.candidate_id
        ) AND NEW.from_stage IS NULL
        THEN RAISE(ABORT, 'only the initial event may omit from_stage')
    END;

    SELECT CASE
        WHEN NEW.from_stage IS NOT NULL AND NEW.from_stage <> (
            SELECT to_stage FROM candidate_stage_events
            WHERE candidate_id = NEW.candidate_id
            ORDER BY id DESC LIMIT 1
        )
        THEN RAISE(ABORT, 'candidate stage changed; reload and retry')
    END;

    SELECT CASE
        WHEN NEW.from_stage IN ('Hired', 'Rejected')
        THEN RAISE(ABORT, 'Hired and Rejected are final outcomes')
    END;

    SELECT CASE
        WHEN NEW.to_stage <> 'Rejected' AND NOT (
            (NEW.from_stage = 'Applied' AND NEW.to_stage = 'Screening') OR
            (NEW.from_stage = 'Screening' AND NEW.to_stage = 'Interview') OR
            (NEW.from_stage = 'Interview' AND NEW.to_stage = 'Offer') OR
            (NEW.from_stage = 'Offer' AND NEW.to_stage = 'Hired')
        )
        THEN RAISE(ABORT, 'candidate stages may advance only one step at a time')
    END;
END;

CREATE TRIGGER candidate_stage_events_no_update
BEFORE UPDATE ON candidate_stage_events
BEGIN
    SELECT RAISE(ABORT, 'candidate stage history is immutable');
END;

CREATE TRIGGER candidate_stage_events_no_delete
BEFORE DELETE ON candidate_stage_events
BEGIN
    SELECT RAISE(ABORT, 'candidate stage history is immutable');
END;
