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
Fry's `login_time` supplies the precise session start and detects a reconnect that
happens entirely between polls.

## Server and pack history

Each successful Fry poll records the normalized pack name and version. The first poll
opens a pack installation for that server. A later name or version change closes the
old installation, opens the new one, and attributes subsequent player sessions to the
new installation. Pack metadata is retained with the version for later inspection.

Admins with **Manage Roles** can use these commands in the admin channel:

- `!serverstats` shows cumulative player-hours, sessions, unique players, and the
  current pack for every enabled server. `!serverstats <server>` filters the report.
- `!packstats` shows installation duration, player-hours, sessions, and unique players
  for recorded pack versions. `!packstats <server>` filters the report.

Statistics begin with EggBot's first successful tracking poll; EggBot does not invent
an earlier installation date. An installation remains current until Fry reports a
different pack name or version. `!help serverstats` and `!help packstats` contain the
same command requirements and usage.
