# Automated database backups

EggBot's backup command uses SQLite's online backup API. It includes committed WAL
transactions without stopping the bot, runs `PRAGMA integrity_check` before publishing
the backup, writes a SHA-256 sidecar, and applies owner-only permissions. The default
systemd service retains the newest 30 daily generations.

Backups contain encrypted Fry credentials and operational Discord data. Keep the
directory private even though Fry secrets are encrypted. The encryption key is not
copied into database backups and must be protected separately; a database backup is
not sufficient for disaster recovery without that key.

## Production paths

The supplied units expect:

- repository: `/home/eggbot/eggBot`
- Python environment: `/home/eggbot/eggBot/.venv`
- database: `/var/lib/eggbot/eggbot.sqlite3`
- backups: `/var/lib/eggbot/backups`
- environment: `/etc/eggbot/eggbot.env`
- encryption key: `/etc/eggbot/secret.key`

Change the unit and environment file together if production uses different paths.

## Installation

As root, create the environment file with `0600` permissions, then install the units:

```bash
install -d -m 0750 -o root -g eggbot /etc/eggbot
install -m 0600 -o root -g eggbot deploy/eggbot.env.example /etc/eggbot/eggbot.env
install -m 0644 deploy/eggbot-backup.service /etc/systemd/system/
install -m 0644 deploy/eggbot-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now eggbot-backup.timer
```

The existing `/etc/eggbot/secret.key` must be owned by `eggbot`, mode `0600`; EggBot
rejects keys accessible by a group or other users. Do not generate a replacement key
when configuring backups.

## Initial verification

Run one backup immediately and inspect the result:

```bash
systemctl start eggbot-backup.service
systemctl status eggbot-backup.service
journalctl -u eggbot-backup.service --since today
systemctl list-timers eggbot-backup.timer
ls -la /var/lib/eggbot/backups
(cd /var/lib/eggbot/backups && sha256sum -c eggbot-*.sqlite3.sha256)
```

Successful command output is one JSON object containing only the backup filename,
checksum, byte count, and pruned filenames. It never prints database contents, Fry
tokens, passwords, or the encryption key.

## Manual usage

```bash
EGGBOT_DB_PATH=/var/lib/eggbot/eggbot.sqlite3 \
EGGBOT_BACKUP_DIR=/var/lib/eggbot/backups \
/home/eggbot/eggBot/.venv/bin/python \
    /home/eggbot/eggBot/scripts/backup_database.py --keep 30
```

Retention runs only after a new backup passes integrity validation and its checksum is
written. It removes only files named `eggbot-YYYYMMDDTHHMMSSZ.sqlite3` and their
matching checksum sidecars from the configured backup directory. Unrelated files are
never pruned.

## Restore drill

Do not overwrite a running database. Stop EggBot, preserve the failed database, verify
the selected backup checksum, copy it into place with owner-only permissions, and then
start EggBot:

```bash
systemctl stop eggbot.service
(cd /var/lib/eggbot/backups && \
    sha256sum -c eggbot-YYYYMMDDTHHMMSSZ.sqlite3.sha256)
install -m 0600 -o eggbot -g eggbot \
    /var/lib/eggbot/backups/eggbot-YYYYMMDDTHHMMSSZ.sqlite3 \
    /var/lib/eggbot/eggbot.sqlite3
systemctl start eggbot.service
systemctl status eggbot.service
```

Run `scripts/validate_database.py` with the protected key after restoration, and retain
the failed database until the cause is understood.
