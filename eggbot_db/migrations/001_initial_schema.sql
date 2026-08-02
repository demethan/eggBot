CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE servers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    endpoint TEXT NOT NULL,
    api_user TEXT,
    api_password_encrypted BLOB,
    api_token_encrypted BLOB,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE server_capabilities (
    server_id INTEGER PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    authentication TEXT NOT NULL DEFAULT 'unknown',
    metadata_read TEXT NOT NULL DEFAULT 'unknown',
    whitelist_read TEXT NOT NULL DEFAULT 'unknown',
    whitelist_write TEXT NOT NULL DEFAULT 'unknown',
    checked_at TEXT
);

CREATE TABLE players (
    id INTEGER PRIMARY KEY,
    minecraft_uuid TEXT UNIQUE,
    current_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE TABLE player_names (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT,
    UNIQUE(player_id, name)
);

CREATE INDEX player_names_name_idx ON player_names(name COLLATE NOCASE);

CREATE TABLE packs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE pack_versions (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
    version TEXT NOT NULL COLLATE NOCASE,
    metadata_json TEXT,
    UNIQUE(pack_id, version)
);

CREATE TABLE pack_installations (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    pack_version_id INTEGER NOT NULL REFERENCES pack_versions(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    start_source TEXT NOT NULL,
    end_source TEXT,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE UNIQUE INDEX one_open_pack_per_server_idx
ON pack_installations(server_id) WHERE ended_at IS NULL;
CREATE INDEX pack_installations_period_idx
ON pack_installations(server_id, started_at, ended_at);

CREATE TABLE server_observations (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    pack_version_id INTEGER REFERENCES pack_versions(id),
    online_count INTEGER CHECK (online_count IS NULL OR online_count >= 0),
    raw_json TEXT,
    UNIQUE(server_id, observed_at)
);

CREATE INDEX server_observations_period_idx
ON server_observations(server_id, observed_at);

CREATE TABLE discord_events (
    id INTEGER PRIMARY KEY,
    discord_message_id INTEGER NOT NULL UNIQUE,
    discord_channel_id INTEGER NOT NULL,
    source_discord_id INTEGER NOT NULL,
    server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    player_name TEXT,
    occurred_at TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    parsed_at TEXT,
    parser_version INTEGER
);

CREATE INDEX discord_events_server_time_idx
ON discord_events(server_id, occurred_at);

CREATE TABLE player_sessions (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    pack_installation_id INTEGER REFERENCES pack_installations(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    start_source TEXT NOT NULL,
    end_source TEXT,
    confidence TEXT NOT NULL CHECK (confidence IN ('exact', 'api_derived', 'estimated', 'recovered', 'incomplete')),
    start_event_id INTEGER REFERENCES discord_events(id) ON DELETE SET NULL,
    end_event_id INTEGER REFERENCES discord_events(id) ON DELETE SET NULL,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE UNIQUE INDEX one_open_session_per_player_server_idx
ON player_sessions(player_id, server_id) WHERE ended_at IS NULL;
CREATE INDEX player_sessions_player_period_idx
ON player_sessions(player_id, started_at, ended_at);
CREATE INDEX player_sessions_server_period_idx
ON player_sessions(server_id, started_at, ended_at);

CREATE TABLE applications (
    id INTEGER PRIMARY KEY,
    discord_user_id INTEGER NOT NULL,
    minecraft_name TEXT NOT NULL COLLATE NOCASE,
    over_18 INTEGER NOT NULL CHECK (over_18 IN (0, 1)),
    sponsor_type TEXT CHECK (sponsor_type IN ('discord', 'minecraft')),
    sponsor_identifier TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'needs_information', 'approved', 'denied', 'partial_failure')),
    admin_channel_id INTEGER,
    admin_message_id INTEGER UNIQUE,
    submitted_at TEXT NOT NULL,
    decided_at TEXT,
    reviewer_discord_user_id INTEGER,
    decision_note TEXT,
    CHECK (
        (sponsor_type IS NULL AND sponsor_identifier IS NULL)
        OR (sponsor_type IS NOT NULL AND sponsor_identifier IS NOT NULL)
    )
);

CREATE INDEX applications_user_time_idx
ON applications(discord_user_id, submitted_at);

CREATE TABLE application_whitelist_results (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'unsupported', 'permission_denied')),
    response_message TEXT,
    UNIQUE(application_id, server_id)
);

CREATE TABLE reboot_schedules (
    server_id INTEGER PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    time_value TEXT NOT NULL,
    frequency TEXT NOT NULL,
    timezone TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
