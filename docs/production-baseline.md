# Production baseline and rollback

This procedure captures EggBot before migration and probes Fry capabilities without changing server or whitelist state.

## Protected backup

Create a timestamped directory outside the repository, readable only by the operator. Back up:

- `/home/eggbot/eggBot`, including `config.json`, `data.json`, and Git metadata.
- `/etc/systemd/system/eggbot.service`.
- The active Python and installed-package versions.
- `systemctl status` and recent service logs.

Generate SHA-256 checksums for every archive. Never attach these archives to a GitHub issue because they contain production credentials and tokens.

## Non-mutating Fry capability audit

Run from a trusted host with network access:

```bash
python3 scripts/fry_capability_audit.py \
  --data /home/eggbot/eggBot/data.json \
  --output fry-capabilities.json
```

The report contains server names and capability states only. It never includes endpoints, passwords, or tokens.

The whitelist write probe submits an empty form. Fry performs authorization and then rejects it with `400 Name not specified` before calling whitelist code. Any unexpected 2xx response is reported as unsafe and must be investigated.

## Rollback

1. Stop `eggbot.service`.
2. Preserve the failed deployment and its database for diagnosis.
3. Restore the protected production archive to `/home/eggbot/eggBot` with original ownership.
4. Restore the previous systemd unit and run `systemctl daemon-reload`.
5. Start `eggbot.service`.
6. Verify Discord login, `!s`, `!o`, `!ls`, Fry metadata access, and application intake.
7. Confirm the restored `data.json` remains valid JSON and receives expected updates.

Database migration must not remove the JSON backup until the production observation window is complete.
