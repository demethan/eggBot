CREATE TABLE application_followup_requests (
    application_id INTEGER PRIMARY KEY REFERENCES applications(id) ON DELETE CASCADE,
    prompt_message_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('offered', 'requested', 'declined')),
    offered_at TEXT NOT NULL,
    responded_at TEXT
);
