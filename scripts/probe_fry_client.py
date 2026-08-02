#!/usr/bin/env python3
"""Run sanitized, read-only integration checks through the shared Fry client."""

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eggbot_db import Database, SecretBox
from eggbot_db.repositories import ServerRepository
from fry_api import FryApiClient


PROBE_NAME = "__eggbot_capability_probe__"


def result_status(result):
    return "supported" if result.ok else result.error.code.value


async def run(args) -> dict:
    database = Database(args.database)
    secret_box = SecretBox.from_file(args.secret_key_file)
    connection = database.connect()
    repository = ServerRepository(connection, secret_box)
    servers = repository.list_enabled()
    if args.force_auth:
        servers = [replace(server, api_token=None) for server in servers]

    def persist_token(server, token):
        repository.update_token(server.id, token)
        connection.commit()

    async def probe(client, server):
        metadata, whitelist = await asyncio.gather(
            client.get_metadata(server),
            client.whitelist_contains(server, PROBE_NAME),
        )
        return {
            "server": server.name,
            "metadata": result_status(metadata),
            "whitelist_read": result_status(whitelist),
            "probe_user_present": whitelist.value if whitelist.ok else None,
        }

    try:
        async with FryApiClient(on_token_refreshed=persist_token) as client:
            results = await asyncio.gather(*(probe(client, server) for server in servers))
        return {"read_only": True, "forced_authentication": args.force_auth, "servers": results}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--secret-key-file", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--force-auth", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if all(
        item["metadata"] == "supported" and item["whitelist_read"] == "supported"
        for item in report["servers"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
