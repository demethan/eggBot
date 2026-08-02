# Shared Fry API client

`FryApiClient` owns one reusable aiohttp session for EggBot's lifetime. It supports token authentication, metadata reads, and whitelist add/check/remove/reload operations.

## Error contract

Callers receive `FryResult` rather than raw aiohttp responses. Errors distinguish:

- invalid request (`400`)
- authentication failure (`401`)
- permission denial (`403`)
- missing resource (`404`)
- rate limiting (`429`)
- Fry server failure (`5xx`)
- timeout
- connection failure
- invalid response
- unsupported capability
- unexpected internal batch failure

Safe reads and token acquisition use bounded retries for transient transport, rate-limit, and server errors. Whitelist mutations are not automatically replayed after ambiguous transport failures. An authorization failure triggers at most one token refresh and one request retry.

## Credential handling

The `Server` representation excludes decrypted passwords and tokens. Requests never log headers or payloads. A token-refresh callback lets the application persist refreshed tokens through `ServerRepository.update_token`, which encrypts them before writing SQLite.

## Capability behavior

Known `unsupported` or `permission_denied` capability flags stop a request before network access. Unknown capability state permits discovery and normal structured error handling.

## Read-only integration probe

The integration probe loads encrypted staging credentials and performs metadata and sentinel whitelist reads only:

```bash
python scripts/probe_fry_client.py \
  --database /protected/path/eggbot-staging.sqlite3 \
  --secret-key-file /protected/path/eggbot-staging.key \
  --report /protected/path/fry-client-probe.json
```

Add `--force-auth` to ignore the imported token cache, obtain fresh tokens through `/v1/token/`, and persist them encrypted through the repository callback.

The report includes server names and capability outcomes only. It does not contain endpoints, metadata, credentials, tokens, or response bodies.
