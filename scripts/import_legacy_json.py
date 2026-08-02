#!/usr/bin/env python3
"""Dry-run or import EggBot's legacy data.json into SQLite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eggbot_db import Database, SecretBox
from eggbot_db.json_importer import ImportValidationError, LegacyJsonImporter, read_source, validate_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--source-timezone", default="America/Montreal")
    parser.add_argument("--secret-key-file", type=Path)
    args = parser.parse_args()

    if not args.dry_run and args.database is None:
        parser.error("--database is required unless --dry-run is used")

    try:
        payload, source_hash, source_size = read_source(args.source)
        errors = validate_payload(payload)
        if errors:
            raise ImportValidationError(errors)

        if args.dry_run:
            server_players = sum(
                len(server.get("players") or {})
                for server in payload["server_list"].values()
            )
            result = {
                "mode": "dry_run",
                "source_sha256": source_hash,
                "source_size": source_size,
                "settings": len(set(payload) - {"server_list", "players", "reboot_schedule"}),
                "servers": len(payload["server_list"]),
                "players": len(payload.get("players", {})),
                "schedules": len(payload.get("reboot_schedule", {})),
                "unresolved_schedules": sum(
                    1
                    for key in payload.get("reboot_schedule", {})
                    if key.casefold()
                    not in {server_name.casefold() for server_name in payload["server_list"]}
                ),
                "legacy_server_players": server_players,
                "validation_errors": [],
            }
        else:
            database = Database(args.database)
            database.migrate()
            with database.connect() as connection:
                secret_box = (
                    SecretBox.from_file(args.secret_key_file)
                    if args.secret_key_file
                    else SecretBox.from_environment()
                )
                importer = LegacyJsonImporter(
                    connection,
                    secret_box,
                    args.source_timezone,
                )
                report = importer.import_file(args.source)
            result = {"mode": "import", **report.sanitized()}
    except ImportValidationError as exc:
        result = {"mode": "failed", "validation_errors": exc.errors}
        rendered = json.dumps(result, indent=2, sort_keys=True)
        print(rendered, file=sys.stderr)
        return 2

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
