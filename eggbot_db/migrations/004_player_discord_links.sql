CREATE TABLE player_discord_links (
    id INTEGER PRIMARY KEY,
    discord_user_id INTEGER NOT NULL,
    discord_username TEXT NOT NULL COLLATE NOCASE,
    discord_display_name TEXT NOT NULL COLLATE NOCASE,
    minecraft_name TEXT NOT NULL COLLATE NOCASE,
    application_id INTEGER NOT NULL UNIQUE
        REFERENCES applications(id) ON DELETE RESTRICT,
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(discord_user_id, minecraft_name)
);

CREATE INDEX player_discord_links_minecraft_idx
ON player_discord_links(minecraft_name COLLATE NOCASE);

CREATE INDEX player_discord_links_discord_name_idx
ON player_discord_links(discord_username COLLATE NOCASE);

CREATE INDEX player_discord_links_display_name_idx
ON player_discord_links(discord_display_name COLLATE NOCASE);
