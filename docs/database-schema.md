# EggBot database design

EggBot uses SQLite as its single-process operational and historical database. Discord commands and Fry clients access data through repositories rather than issuing SQL directly.

## Safety defaults

- Foreign-key enforcement is enabled on every connection.
- WAL mode permits readers while EggBot records observations.
- Writes use transactions and a five-second busy timeout.
- Forward migrations are numbered, ordered, transactional, and recorded in `schema_migrations`.
- The database and online backups use owner-only `0600` permissions.
- Backups use SQLite's backup API, which produces a consistent copy including committed WAL data.
- All timestamps are UTC ISO-8601 strings. Presentation converts them to Discord-local timestamps.

## Secrets

Fry passwords and cached bearer tokens are encrypted before they enter SQLite. The Fernet key is supplied through `EGGBOT_SECRET_KEY` in the systemd environment and must not be stored beside the database or committed to Git.

Generate a key during deployment with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Losing this key makes stored Fry credentials unrecoverable. It must be included in the protected operations backup but never in ordinary database exports.

## Main relationships

- `servers` owns API configuration and encrypted credentials.
- `server_capabilities` records the most recent capability audit per server.
- `players` provides stable identity; `player_names` preserves rename history.
- `server_observations` stores authoritative Fry snapshots.
- `discord_events` stores authenticated raw notification events for deduplication and reparsing.
- `player_sessions` combines players, servers, optional pack installations, source events, and confidence.
- `packs`, `pack_versions`, and `pack_installations` retain modpack history.
- `applications` and `application_whitelist_results` preserve review and per-server audit results.
- `settings` replaces mutable configuration values previously stored in `data.json`.
- `reboot_schedules` stores validated server schedules.

Statistics are derived from historical sessions and observations instead of stored totals. This permits corrections when reconciliation improves.

## Invariants

- A player can have at most one open session on a server.
- A server can have at most one open pack installation.
- Session and installation end times cannot precede start times.
- Discord message IDs and admin application message IDs are unique.
- Application sponsor type and identifier must both be present or both absent.
- Confidence values are limited to exact, API-derived, estimated, recovered, and incomplete.

The JSON importer is intentionally separate from the schema migration framework and is tracked in issue #28.
