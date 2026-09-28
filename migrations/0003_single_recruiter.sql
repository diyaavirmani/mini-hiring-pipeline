CREATE TRIGGER recruiters_allow_single_account
BEFORE INSERT ON recruiters
WHEN EXISTS (SELECT 1 FROM recruiters)
BEGIN
    SELECT RAISE(ABORT, 'only one recruiter account is supported');
END;
