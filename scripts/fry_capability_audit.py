#!/usr/bin/env python3
"""Safely audit Fry API capabilities without changing server state."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, parse, request


PROBE_NAME = "__eggbot_capability_probe__"


@dataclass
class ServerCapabilities:
    server: str
    authentication: str = "not_tested"
    metadata_read: str = "not_tested"
    whitelist_read: str = "not_tested"
    whitelist_write: str = "not_tested"


def http_request(
    method: str,
    url: str,
    *,
    data: Optional[Dict[str, str]] = None,
    token: Optional[str] = None,
    timeout: float = 10.0,
) -> Tuple[int, bytes]:
    body = parse.urlencode(data).encode() if data is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_request = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(
            api_request,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def acquire_token(server: Dict[str, Any], timeout: float) -> Tuple[str, Optional[str]]:
    user = server.get("id") or server.get("user")
    password = server.get("password")
    cached_token = server.get("token")

    if user and password:
        status, body = http_request(
            "POST",
            endpoint(server["endpoint"], "/v1/token/"),
            data={"id": str(user), "password": str(password)},
            timeout=timeout,
        )
        if status == 200:
            try:
                token = json.loads(body)["data"]["token"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return "invalid_response", None
            return "authenticated", token
        if status in (401, 403):
            return "denied", None
        return f"http_{status}", None

    if cached_token:
        return "cached_token", str(cached_token)
    return "missing_credentials", None


def classify_read(status: int) -> str:
    if status == 200:
        return "supported"
    if status == 404:
        return "supported_not_found"
    if status in (401, 403):
        return "permission_denied"
    return f"http_{status}"


def classify_write_probe(status: int) -> str:
    # Fry validates the missing name before invoking whitelist mutation.
    if status == 400:
        return "supported"
    if status in (401, 403):
        return "permission_denied"
    if status == 404:
        return "unsupported"
    if 200 <= status < 300:
        return "unsafe_unexpected_success"
    return f"http_{status}"


def audit_server(name: str, server: Dict[str, Any], timeout: float) -> ServerCapabilities:
    result = ServerCapabilities(server=name)
    if not server.get("endpoint"):
        result.authentication = "missing_endpoint"
        return result

    try:
        result.authentication, token = acquire_token(server, timeout)
        if token is None:
            return result

        meta_status, _ = http_request(
            "GET",
            endpoint(server["endpoint"], "/v1/meta/"),
            token=token,
            timeout=timeout,
        )
        result.metadata_read = classify_read(meta_status)

        read_status, _ = http_request(
            "GET",
            endpoint(server["endpoint"], f"/v1/whitelist/{PROBE_NAME}/"),
            token=token,
            timeout=timeout,
        )
        result.whitelist_read = classify_read(read_status)

        # An empty form is deliberately invalid. Fry checks WRITE_WHITELIST first,
        # then returns 400 "Name not specified" without touching the whitelist.
        write_status, _ = http_request(
            "POST",
            endpoint(server["endpoint"], "/v1/whitelist/"),
            data={},
            token=token,
            timeout=timeout,
        )
        result.whitelist_write = classify_write_probe(write_status)
    except (error.URLError, TimeoutError, OSError, ValueError):
        result.authentication = "connection_error"

    return result


def load_servers(data_path: Path) -> Dict[str, Dict[str, Any]]:
    with data_path.open(encoding="utf-8") as data_file:
        payload = json.load(data_file)
    servers = payload.get("server_list")
    if not isinstance(servers, dict):
        raise ValueError("data file does not contain a server_list object")
    return servers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Path to EggBot data.json")
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON report path")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout")
    args = parser.parse_args()

    try:
        servers = load_servers(args.data)
        report = {
            "probe_is_non_mutating": True,
            "servers": [
                asdict(audit_server(name, server, args.timeout))
                for name, server in sorted(servers.items())
            ],
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Capability audit failed: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
