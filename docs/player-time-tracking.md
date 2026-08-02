# Player time tracking

EggBot listens only in the configured `generalChannelID` and accepts join/leave
notifications only from Discord bot accounts whose display name matches an enabled
server in SQLite. Normal relayed chat is ignored.

Supported notifications include `Player has joined the server.` and `Player has left
the server.` (plus equivalent `the game` variants). Discord message IDs make ingestion
idempotent. A join opens one exact session per player/server; a leave closes it. A
leave without a known join is retained as an event without inventing play time.

Admins with **Manage Roles** can use `!hours` in the admin channel for all current-week
hours, or `!hours <Minecraft IGN>` for one player. Weeks begin Monday at midnight in
`America/Montreal`. Sessions crossing the week boundary are clipped correctly and
open sessions count through the report time and are labeled.
