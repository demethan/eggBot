# Membership application workflow

EggBot is a companion client for FryingPan2. The supported compatibility baseline is
[`segfaulted/fryingpan2` branch `2021-update`](https://github.com/segfaulted/fryingpan2/tree/2021-update),
commit `cbecebeaac7c6af196cdc9e4318a844e13f57e52`.

Applications are saved in SQLite before they are posted for review. A user may have
only one open application. Applicants provide their Minecraft IGN, whether they are
18 or older, where they found the community, and—when applicable—a sponsor identified
by either Discord name or Minecraft IGN.

An administrator or member with the Discord **Manage Roles** permission reviews the
application using the reactions on its admin-channel message. Approval checks and adds
the IGN directly through each enabled FryingPan2 API. The member role is assigned only
after every server succeeds. Per-server successes are retained so a later retry calls
only failed servers. Simultaneous review attempts are locked in SQLite.

The original admin message is edited with the final or retry status and is never
deleted. If role assignment fails after whitelisting, the application returns to a
retryable state; completed FryingPan2 calls are not repeated.

The `2021-update` API contract used by EggBot is:

- `POST /v1/token/` for JWT authentication
- `GET /v1/whitelist/{name}/` to check membership
- `POST /v1/whitelist/` with form field `name` to add a player
- `PATCH /v1/whitelist/` to reload the whitelist when explicitly needed

Production verification must use a dedicated test IGN. Development tests use a fake
client and must never change a production whitelist.

Runtime configuration requires `EGGBOT_DB_PATH` plus either `EGGBOT_SECRET_KEY` or an
owner-only key file selected by `EGGBOT_SECRET_KEY_FILE`. Startup applies forward-only
database migrations before connecting Discord and keeps one shared Fry API session.
