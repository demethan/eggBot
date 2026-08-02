CREATE TABLE application_decision_locks (
    application_id INTEGER PRIMARY KEY REFERENCES applications(id) ON DELETE CASCADE,
    reviewer_discord_user_id INTEGER NOT NULL,
    claimed_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_open_application_per_user_idx
ON applications(discord_user_id)
WHERE status IN ('pending', 'needs_information', 'partial_failure');
