CREATE TABLE fry_health_state (
    server_id INTEGER PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    outage_started_at TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_error TEXT
);

CREATE TABLE fry_connectivity_incidents (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    alerted_at TEXT,
    recovered_at TEXT,
    recovery_notified_at TEXT,
    affected_servers_json TEXT NOT NULL,
    last_error TEXT,
    alert_channel_id INTEGER,
    alert_message_id INTEGER
);

CREATE UNIQUE INDEX one_open_fry_connectivity_incident_idx
ON fry_connectivity_incidents((1)) WHERE recovered_at IS NULL;

CREATE INDEX fry_connectivity_incidents_period_idx
ON fry_connectivity_incidents(started_at, recovered_at);
