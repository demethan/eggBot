# Production cutover runbook

This document is the handoff for a Codex session running as the Linux user `eggbot`.
The current production bot is under `/home/eggbot/eggBot`. Downtime is acceptable;
prefer a careful, reversible cutover over parallel runtimes or a zero-downtime swap.

## Instructions for the new Codex session

Start with a read-only audit. Do not stop services, overwrite files, expose secrets,
change whitelists, or install system units until the operator explicitly approves the
cutover. Never print `config.json`, Fry passwords/tokens, the Discord token, or the
encryption key into the conversation or command output.

Use the `update2026` branch from `git@github.com:demethan/eggBot.git`. Run the complete
test suite and security checks from the candidate release before requesting downtime.
Preserve the current production directory and service unit until the observation
window is complete.

## Target layout

- active code: `/home/eggbot/eggBot`
- candidate before cutover: `/home/eggbot/eggBot-update2026`
- rollback archive/directory: `/home/eggbot/cutover-backups/`
- SQLite state: `/var/lib/eggbot/eggbot.sqlite3`
- automatic backups: `/var/lib/eggbot/backups/`
- service environment: `/etc/eggbot/eggbot.env`
- encryption key: `/etc/eggbot/secret.key`
- service: `/etc/systemd/system/eggbot.service`

Writing `/var/lib`, `/etc`, or systemd requires root authority. The Codex session must
pause and request approval for those steps if it does not have passwordless scoped
sudo. Do not broaden permissions on secrets to work around an access failure.

## Phase 1: read-only production audit

Record commands and results without displaying file contents containing secrets:

```bash
id
pwd
systemctl status eggbot.service --no-pager
systemctl cat eggbot.service
systemctl show eggbot.service \
    -p User -p Group -p WorkingDirectory -p ExecStart -p EnvironmentFiles
ls -ld /home/eggbot /home/eggbot/eggBot
stat -c '%a %U %G %n' \
    /home/eggbot/eggBot/config.json \
    /home/eggbot/eggBot/data.json
git -C /home/eggbot/eggBot status --short --branch
git -C /home/eggbot/eggBot rev-parse HEAD
python3 --version
python3.12 --version
df -h /home/eggbot /var/lib /etc
```

Also capture recent logs, filtering nothing into a public issue:

```bash
journalctl -u eggbot.service --since '24 hours ago' --no-pager
```

If logs expose credentials or bearer tokens, do not paste them into chat. Report only
the relevant error class and timestamp.

Resolve any differences between the discovered paths and this runbook before
continuing. Confirm the service truly runs as `eggbot`.

## Phase 2: prepare the candidate while production is online

Do not alter `/home/eggbot/eggBot` during this phase.

```bash
cd /home/eggbot
git clone --branch update2026 --single-branch \
    git@github.com:demethan/eggBot.git eggBot-update2026
cd /home/eggbot/eggBot-update2026
git status --short --branch
git log -1 --oneline
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip pipenv
.venv/bin/pipenv requirements > /tmp/eggbot-update2026-requirements.txt
.venv/bin/python -m pip install \
    -r /tmp/eggbot-update2026-requirements.txt
.venv/bin/python -m unittest discover -s tests -q
```

Delete the temporary requirements rendering after installation. Installation must use
the committed `Pipfile.lock`; do not regenerate or upgrade the lock during cutover.

Run the dependency audit using an isolated audit environment or an already available
`pip-audit`, then audit the candidate environment:

```bash
pip-audit --path \
    /home/eggbot/eggBot-update2026/.venv/lib/python3.12/site-packages \
    --progress-spinner off
```

Expected project tests currently pass, and the audit must report no known runtime
vulnerabilities. Stop and investigate any failure rather than deploying around it.

Run the Fry capability audit from the candidate against a protected copy of production
`data.json`. It performs authenticated reads and a non-mutating rejected write probe;
its report excludes endpoints and secrets. Do not perform a real whitelist mutation:

```bash
mkdir -m 0700 -p /home/eggbot/cutover-work
install -m 0600 /home/eggbot/eggBot/data.json \
    /home/eggbot/cutover-work/data.preflight.json
cd /home/eggbot/eggBot-update2026
.venv/bin/python scripts/fry_capability_audit.py \
    --data /home/eggbot/cutover-work/data.preflight.json \
    --output /home/eggbot/cutover-work/fry-capabilities.json
```

Do not start `eggbot.py` from the candidate while the production bot is running.

## Phase 3: request and begin downtime

Before stopping production, tell the operator exactly what will happen and obtain
approval. Choose an explicit timestamp such as `20260803T020000Z` and use it consistently
in place of `CUTOVER_TIMESTAMP` below; do not rely on an empty shell variable.

As root or through approved sudo:

```bash
systemctl stop eggbot.service
systemctl is-active eggbot.service
```

The expected result is `inactive`. Confirm no EggBot process remains before copying
the final mutable JSON files.

Create a protected rollback set:

```bash
install -d -m 0700 -o eggbot -g eggbot \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP
tar --acls --xattrs -C /home/eggbot -czf \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/eggBot-production.tar.gz \
    eggBot
cp --preserve=mode,ownership,timestamps \
    /etc/systemd/system/eggbot.service \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/eggbot.service
sha256sum \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/eggBot-production.tar.gz \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/eggbot.service \
    > /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/SHA256SUMS
chmod 0600 \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/eggBot-production.tar.gz \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/eggbot.service \
    /home/eggbot/cutover-backups/CUTOVER_TIMESTAMP/SHA256SUMS
```

Verify `sha256sum -c SHA256SUMS` from inside that directory before proceeding.

## Phase 4: final JSON import

Copy the final stopped-production configuration into the candidate without printing it:

```bash
install -m 0600 /home/eggbot/eggBot/config.json \
    /home/eggbot/eggBot-update2026/config.json
install -m 0600 /home/eggbot/eggBot/data.json \
    /home/eggbot/eggBot-update2026/data.json
```

Create protected state directories and the encryption key only if the key does not
already exist. `generate_secret_key.py` intentionally refuses to overwrite a key:

```bash
install -d -m 0700 -o eggbot -g eggbot /var/lib/eggbot
install -d -m 0700 -o eggbot -g eggbot /var/lib/eggbot/backups
install -d -m 0750 -o root -g eggbot /etc/eggbot
/home/eggbot/eggBot-update2026/.venv/bin/python \
    /home/eggbot/eggBot-update2026/scripts/generate_secret_key.py \
    /etc/eggbot/secret.key
chown eggbot:eggbot /etc/eggbot/secret.key
```

The key must be owned by `eggbot` and mode `0600`. If it already exists, verify it and
reuse it. Never replace an existing key associated with a database.

Ensure `/var/lib/eggbot/eggbot.sqlite3` does not contain needed data before creating a
fresh production database. If it exists, stop and resolve it explicitly; never delete
or overwrite it automatically.

Dry-run, import, and validate:

```bash
cd /home/eggbot/eggBot-update2026
.venv/bin/python scripts/import_legacy_json.py \
    --source data.json --dry-run \
    --report /home/eggbot/cutover-work/import-dry-run.json
.venv/bin/python scripts/import_legacy_json.py \
    --source data.json \
    --database /var/lib/eggbot/eggbot.sqlite3 \
    --secret-key-file /etc/eggbot/secret.key \
    --report /home/eggbot/cutover-work/import-report.json
.venv/bin/python scripts/validate_database.py \
    --database /var/lib/eggbot/eggbot.sqlite3 \
    --secret-key-file /etc/eggbot/secret.key \
    --report /home/eggbot/cutover-work/database-validation.json
```

Require database integrity `ok`, migration completion, successful secret decryption,
and sensible imported counts before switching code directories.

## Phase 5: switch the code directory

Keep the old directory recoverable instead of editing it in place:

```bash
mv /home/eggbot/eggBot \
    /home/eggbot/eggBot-legacy-CUTOVER_TIMESTAMP
mv /home/eggbot/eggBot-update2026 /home/eggbot/eggBot
chown -R eggbot:eggbot /home/eggbot/eggBot
```

Resolve and review both paths immediately before each move. Do not use globs or a
recursive deletion command.

Create `/etc/eggbot/eggbot.env`, owned by root and mode `0600`:

```text
EGGBOT_DB_PATH=/var/lib/eggbot/eggbot.sqlite3
EGGBOT_BACKUP_DIR=/var/lib/eggbot/backups
EGGBOT_SECRET_KEY_FILE=/etc/eggbot/secret.key
```

Install a service unit using these essential settings, preserving any production
WireGuard ordering discovered during the audit:

```ini
[Unit]
Description=EggBot Discord bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=eggbot
Group=eggbot
WorkingDirectory=/home/eggbot/eggBot
EnvironmentFile=/etc/eggbot/eggbot.env
ExecStart=/home/eggbot/eggBot/.venv/bin/python -u /home/eggbot/eggBot/eggbot.py
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Install the supplied backup units from `deploy/`, review all hardcoded paths, then run:

```bash
systemctl daemon-reload
systemctl enable eggbot.service
systemctl start eggbot.service
systemctl status eggbot.service --no-pager
journalctl -u eggbot.service --since '10 minutes ago' --no-pager
```

## Phase 6: smoke-test gate

Require all of the following before declaring success:

- Discord login succeeds with no reconnect loop.
- Immediate Fry poll succeeds on all enabled servers.
- No false Fry/WireGuard outage alert appears.
- `!s`, `!o`, and `!ls` work.
- `!hours`, `!serverstats`, and `!packstats` work.
- A server-specific statistics report arrives by DM.
- `!whois` works for an existing approved association if one exists after import.
- Application intake opens and can be cancelled without submission.
- Admin and support channel restrictions are correct.
- Whitelist reads work. Do not mutate production whitelist state merely for smoke
  testing; use a dedicated approved IGN only with explicit operator approval.

Test one online backup:

```bash
systemctl start eggbot-backup.service
systemctl status eggbot-backup.service --no-pager
(cd /var/lib/eggbot/backups && sha256sum -c eggbot-*.sqlite3.sha256)
systemctl enable --now eggbot-backup.timer
systemctl list-timers eggbot-backup.timer
```

## Rollback

Rollback if startup, Discord, database validation, Fry access, or core command checks
fail and cannot be corrected quickly.

1. Stop the new service.
2. Preserve the failed new database and logs for diagnosis.
3. Move `/home/eggbot/eggBot` to an explicit failed-release name.
4. Move `/home/eggbot/eggBot-legacy-CUTOVER_TIMESTAMP` back to
   `/home/eggbot/eggBot`.
5. Restore the saved old `eggbot.service`.
6. Run `systemctl daemon-reload` and start `eggbot.service`.
7. Verify the legacy bot reconnects and its JSON remains intact.

Do not delete the failed database, the rollback archive, the legacy directory, or the
final JSON snapshot during rollback.

## Observation and completion

Observe production for at least 24 hours, preferably 48:

- Fry polls and health state remain stable.
- Player sessions open, split on reconnect, and close as expected.
- Pack installations remain consistent.
- Application decisions and whitelist audit records persist.
- Daily backup timer produces a verified generation.
- No secret appears in logs.

After the operator accepts the observation window, merge `update2026` into `master`.
Keep at least one protected pre-cutover archive and the encryption key according to the
operations retention policy.

## Suggested opening prompt

The operator can begin the new session with:

> Work through `/home/eggbot/eggBot/docs/production-cutover.md`. Start with the
> read-only audit. Production downtime is acceptable, but do not stop or modify the
> running bot until I explicitly approve the cutover. Never display secrets.

If the old checkout does not yet contain this file, inspect it from a candidate clone
of `origin/update2026` before beginning.
