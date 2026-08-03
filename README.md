# EggBot

EggBot is the Discord companion for BreakfastCraft Minecraft servers managed by
FryingPan2. It handles membership applications and whitelisting, reports live server
information, tracks player sessions and modpack installations, and alerts
administrators when Fry connectivity fails.

Production runs the `master` branch on Python 3.12. Persistent state and mutable bot
configuration live in SQLite. `data.json` is retained only as a legacy compatibility
and recovery source while the final runtime readers are migrated.

## Main commands

Member commands include:

- `!s [server]` — server/modpack status and recent player activity
- `!o` — players currently online
- `!ls [Minecraft IGN]` — last-seen information
- `!c [server]` — connection information by DM
- `!schedule <IANA timezone>` — reboot schedules
- `!roll [NdS]` — dice roller
- `!apply` — membership application flow

Server information and fun commands require the configured member role. `!apply`
remains available to non-members in the support channel. Administrator commands
require the configured admin channel plus Manage Roles or Administrator permission.

Administrator commands include:

- `!hours [Minecraft IGN]` — current-week player hours
- `!serverstats [server] [week|month|year|all]`
- `!packstats [server] [week|month|year|all]`
- `!whois <Discord user|Minecraft IGN>`
- `!whitelist <Minecraft IGN>` and `!unwhitelist <Minecraft IGN>`
- `!pendingapps`
- `!updateids`, `!set`, `!add`, `!remove`, and `!set_schedule`
- `!setsource <Discord ID> <server>` — authorize a Fry notification source

Discord's `!help` command is the authoritative command reference and includes current
usage details.

## Local setup

Create `config.json` from `sample_config.json` and supply the Discord bot token. Never
commit this file. Configure the database and encryption key through environment
variables:

```text
EGGBOT_DB_PATH=/protected/path/eggbot.sqlite3
EGGBOT_SECRET_KEY_FILE=/protected/path/secret.key
```

Create a virtual environment and install the committed lock file:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install pipenv
.venv/bin/pipenv sync
.venv/bin/python -m unittest discover -s tests -q
```

Generate a new key only for a new database:

```bash
.venv/bin/python scripts/generate_secret_key.py /protected/path/secret.key
```

Never replace the key belonging to an existing database; Fry credentials stored in
that database depend on it.

## Documentation

- [Database schema](docs/database-schema.md)
- [Database-backed configuration](docs/database-configuration.md)
- [Runtime configuration and notification sources](docs/runtime-configuration.md)
- [Database backups](docs/database-backups.md)
- [Player-time tracking](docs/player-time-tracking.md)
- [Engagement metrics](docs/engagement-metrics.md)
- [Application workflow](docs/application-workflow.md)
- [Fry connectivity monitoring](docs/fry-connectivity-monitoring.md)
- [Dependency security](docs/dependency-security.md)
- [Production operations and historical cutover](docs/production-cutover.md)

`sample_data.json` is a sanitized example of the legacy import structure. It is not
loaded in production and contains no usable credentials.
