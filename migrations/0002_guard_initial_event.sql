DROP TRIGGER candidate_stage_events_validate_insert;

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
        WHEN NEW.from_stage IS NOT NULL
         AND NEW.to_stage <> 'Rejected'
         AND NOT (
            (NEW.from_stage = 'Applied' AND NEW.to_stage = 'Screening') OR
            (NEW.from_stage = 'Screening' AND NEW.to_stage = 'Interview') OR
            (NEW.from_stage = 'Interview' AND NEW.to_stage = 'Offer') OR
            (NEW.from_stage = 'Offer' AND NEW.to_stage = 'Hired')
        )
        THEN RAISE(ABORT, 'candidate stages may advance only one step at a time')
    END;
END;
