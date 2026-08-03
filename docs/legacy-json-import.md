# Legacy JSON import

> Production migration is complete. This importer is retained for disaster recovery,
> validation, and development fixtures. Do not run it as part of a normal pull,
> restart, or deployment, and never import over the active production database.

The importer copies `data.json` into the migration-managed SQLite schema without changing the source file. It never includes endpoints, API users, passwords, tokens, Discord IDs, or message content in its report.

## Dry run

Dry run validates structure and reports only counts, size, and checksum. It needs neither a database nor an encryption key.

```bash
python scripts/import_legacy_json.py \
  --source /home/eggbot/eggBot/data.json \
  --dry-run \
  --report import-dry-run.json
```

## Staging import

Generate a protected staging key once:

```bash
python scripts/generate_secret_key.py /protected/path/eggbot-staging.key
```

Reference the protected key file directly, then import into a non-production database:

```bash
python scripts/import_legacy_json.py \
  --source /home/eggbot/eggBot/data.json \
  --database /protected/path/eggbot-staging.sqlite3 \
  --secret-key-file /protected/path/eggbot-staging.key \
  --report /protected/path/import-report.json
```

The importer:

- Validates the entire source before opening a write transaction.
- Encrypts Fry passwords and cached bearer tokens before insertion.
- Imports ordinary settings, servers, players, name history, and schedules.
- Preserves schedules keyed by Discord mentions in an unresolved legacy table until they can be mapped safely to Fry servers.
- Preserves legacy per-server login/duration records for later reconciliation without treating them as confirmed sessions.
- Records the source SHA-256 and category counts in `import_runs`.
- Treats an identical source checksum as already imported.
- Upserts changed snapshots without duplicating logical servers, players, settings, or schedules.
- Rolls back every category if any write fails.

Naive legacy `last_seen` values are interpreted in `America/Montreal` by default and converted to UTC. Override this with `--source-timezone` if the production host used a different local timezone.

## Production cutover

The staging database is disposable and will become stale while the current bot continues writing JSON. At production cutover:

1. Stop EggBot.
2. Back up and checksum the final JSON snapshot.
3. Run dry-run validation.
4. Import into a fresh production database with the production encryption key.
5. Validate counts, database integrity, and API credential decryption.
6. Start the migrated release and run smoke tests.
7. Retain JSON and its checksum through the rollback observation window.

Validate an imported database without displaying secrets:

```bash
python scripts/validate_database.py \
  --database /protected/path/eggbot-staging.sqlite3 \
  --secret-key-file /protected/path/eggbot-staging.key \
  --report /protected/path/database-validation.json
```
