# Player time tracking

EggBot listens only in the configured `generalChannelID` and accepts join/leave
notifications only from Discord bot accounts whose display name matches an enabled
server in SQLite. Normal relayed chat is ignored.

When a notification webhook has a generic name such as `Bridge` or `Server`, map its
Discord webhook ID to an EggBot server name in `serverNotificationSources`. Example:

```json
"serverNotificationSources": {
  "1517681380295835789": "bacon"
}
```

Unmapped generic sources are rejected rather than assigning play time to the wrong
server.

Supported notifications include `Player has joined the server.` and `Player has left
the server.` (plus equivalent `the game` variants). Discord message IDs make ingestion
idempotent. A join opens one exact session per player/server; a leave closes it. A
leave without a known join is retained as an event without inventing play time.

Admins with **Manage Roles** can use `!hours` in the admin channel for all current-week
hours, or `!hours <Minecraft IGN>` for one player. Weeks begin Monday at midnight in
`America/Montreal`. Sessions crossing the week boundary are clipped correctly and
open sessions count through the report time and are labeled.

Every five minutes, the existing Fry metadata poll is also stored as a server
observation and reconciled with open sessions. An online player without a Discord join
starts an `api_derived` session. A missing player is closed only after two consecutive
successful observations report them absent, limiting false logouts from one empty or
glitched API response. API reconciliation never duplicates an open Discord session.
