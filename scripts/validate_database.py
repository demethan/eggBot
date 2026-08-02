#!/usr/bin/env python3
"""Validate an imported EggBot database without displaying secrets."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eggbot_db import Database, SecretBox
from eggbot_db.validation import validate_imported_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--secret-key-file", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    database = Database(args.database)
    with database.connect() as connection:
        report = validate_imported_database(
            connection,
            args.database,
            SecretBox.from_file(args.secret_key_file),
        )

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
