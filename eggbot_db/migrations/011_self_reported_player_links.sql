ALTER TABLE player_discord_links RENAME TO player_discord_links_legacy;

CREATE TABLE player_discord_links (
    id INTEGER PRIMARY KEY,
    discord_user_id INTEGER NOT NULL,
    discord_username TEXT NOT NULL COLLATE NOCASE,
    discord_display_name TEXT NOT NULL COLLATE NOCASE,
    minecraft_name TEXT NOT NULL COLLATE NOCASE,
    application_id INTEGER UNIQUE
        REFERENCES applications(id) ON DELETE RESTRICT,
    link_source TEXT NOT NULL DEFAULT 'approved_application'
        CHECK (link_source IN ('approved_application', 'self_reported')),
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(discord_user_id, minecraft_name),
    CHECK (
        (link_source = 'approved_application' AND application_id IS NOT NULL)
        OR (link_source = 'self_reported' AND application_id IS NULL)
    )
);

INSERT INTO player_discord_links(
    id, discord_user_id, discord_username, discord_display_name,
    minecraft_name, application_id, link_source, linked_at, updated_at
)
SELECT id, discord_user_id, discord_username, discord_display_name,
       minecraft_name, application_id, 'approved_application', linked_at, updated_at
FROM player_discord_links_legacy;

DROP TABLE player_discord_links_legacy;

CREATE INDEX player_discord_links_discord_user_idx
ON player_discord_links(discord_user_id, updated_at DESC);

CREATE INDEX player_discord_links_discord_name_idx
ON player_discord_links(discord_username COLLATE NOCASE);

CREATE INDEX player_discord_links_display_name_idx
ON player_discord_links(discord_display_name COLLATE NOCASE);
