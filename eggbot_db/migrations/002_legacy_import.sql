CREATE TABLE import_runs (
    source_sha256 TEXT PRIMARY KEY,
    source_size INTEGER NOT NULL CHECK (source_size >= 0),
    imported_at TEXT NOT NULL,
    settings_count INTEGER NOT NULL,
    server_count INTEGER NOT NULL,
    player_count INTEGER NOT NULL,
    schedule_count INTEGER NOT NULL,
    unresolved_schedule_count INTEGER NOT NULL,
    legacy_server_player_count INTEGER NOT NULL
);

CREATE TABLE legacy_reboot_schedules (
    id INTEGER PRIMARY KEY,
    schedule_key TEXT NOT NULL,
    time_value TEXT NOT NULL,
    frequency TEXT NOT NULL,
    timezone TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    source_sha256 TEXT NOT NULL REFERENCES import_runs(source_sha256),
    UNIQUE(schedule_key, source_sha256)
);

CREATE TABLE legacy_server_player_state (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    login_time_raw TEXT,
    duration_minutes REAL,
    imported_at TEXT NOT NULL,
    source_sha256 TEXT NOT NULL REFERENCES import_runs(source_sha256),
    UNIQUE(server_id, player_id, login_time_raw, source_sha256)
);

CREATE INDEX legacy_server_player_lookup_idx
ON legacy_server_player_state(server_id, player_id, imported_at);
