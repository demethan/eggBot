CREATE TABLE application_denial_whitelist_checks (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('present', 'absent', 'failed', 'unsupported', 'permission_denied')
    ),
    response_message TEXT,
    UNIQUE(application_id, server_id)
);
