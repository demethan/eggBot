CREATE TABLE whitelist_admin_actions (
    id INTEGER PRIMARY KEY,
    requested_by_discord_user_id INTEGER NOT NULL,
    minecraft_name TEXT NOT NULL COLLATE NOCASE,
    action TEXT NOT NULL CHECK (action IN ('remove')),
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN ('removed', 'absent', 'failed', 'unsupported', 'permission_denied')
    ),
    response_message TEXT,
    attempted_at TEXT NOT NULL
);

CREATE INDEX whitelist_admin_actions_name_time_idx
ON whitelist_admin_actions(minecraft_name COLLATE NOCASE, attempted_at);
