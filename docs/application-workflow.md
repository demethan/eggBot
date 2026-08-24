# Membership application workflow

EggBot is a companion client for FryingPan2. The supported compatibility baseline is
[`segfaulted/fryingpan2` branch `2021-update`](https://github.com/segfaulted/fryingpan2/tree/2021-update),
commit `cbecebeaac7c6af196cdc9e4318a844e13f57e52`.

Applications are saved in SQLite before they are posted for review. A user may have
only one open application. Applicants provide their Minecraft IGN, whether they are
18 or older, where they found the community, and—when applicable—a sponsor identified
by either Discord name or Minecraft IGN.

Sponsor entry asks for the name first, then presents DM reactions: 💬 for a Discord
name, 🎮 for a Minecraft IGN, or 🔴 to cancel the application.

All yes/no questions use reactions: ✅ for yes, 🚫 for no, and 🔴 to cancel the
application.

The discovery-source question also uses reactions and stores a consistent reporting
label: 🔎 `Web search`, 📦 `Modpack search`, 🧑 `Sponsor / friend`, or ▶️ `YouTube`.

The initial admin-channel post mentions the role configured by
`applicationReviewerRoleID`. Allowed mentions are restricted to that one role; status
edits and retries do not send additional notifications.

An administrator or member with the Discord **Manage Roles** permission reviews the
application using the reactions on its admin-channel message. Approval checks and adds
the IGN directly through each enabled FryingPan2 API. The member role is assigned only
after every server succeeds. Per-server successes are retained so a later retry calls
only failed servers. Simultaneous review attempts are locked in SQLite.

On reconnect, EggBot reconciles pending review messages. One unambiguous authorized
thumb reaction received while the bot was offline is processed automatically;
conflicting decisions are left pending for an admin to resolve. Retryable partial
failures are never retried merely because the bot restarted.

The original admin message is edited with the final or retry status and is never
deleted. If role assignment fails after whitelisting, the application returns to a
retryable state; completed FryingPan2 calls are not repeated.

Before assigning the role, EggBot verifies that the configured role exists, is not
integration-managed, and is below EggBot's highest Discord role. EggBot must have
Manage Roles permission (or Administrator). After Discord accepts the change, EggBot
fetches the applicant again and verifies that the role is present. A failure reason is
shown on the retained application message so an admin can correct it and retry.

Final review details show the reviewer and decision together (`Approved` or `Denied`),
followed by the per-server whitelist results.

After denial, EggBot performs a read-only check of every enabled FryingPan whitelist
and stores the per-server result. If the denied IGN is still present, the retained
application message is annotated and the configured reviewer role receives an alert.
EggBot does not remove the IGN automatically.

The denied applicant receives a private reaction prompt: 📩 requests an admin
follow-up and 🔴 declines. Requests are stored in SQLite and notify both the reviewer
role and denying admin with the applicant's Discord account, IGN, and application ID.
An admin can then choose to contact the applicant, subject to Discord's privacy
settings.

Follow-up delivery is durable: a request is marked notified only after the admin alert
succeeds. Undelivered requests are retried after EggBot reconnects. In DMs, EggBot
removes only its own prompt reactions because Discord does not allow bulk reaction
clearing in private channels.

Admins with **Manage Roles** can use `!unwhitelist <Minecraft IGN>` in the admin
channel. The command requires a ✅ reaction confirmation, checks each enabled server
before removal, sends one removal through the first server where the IGN is present,
then waits and re-checks every server to verify the shared whitelist propagated. If a
server still reports the IGN after the bounded wait, that server receives one targeted
removal and all servers are verified again. Every result is stored as a per-server
audit record. 🔴 cancels without making changes.

`!whitelist <Minecraft IGN>` mirrors that safety model for additions: ✅ confirms,
one server receives the add request, EggBot waits and re-checks every enabled server
for shared-list propagation, then targets only servers where the IGN remains absent.
All servers are verified again and every result is audited. 🔴 cancels without making
changes.

After the role is assigned, EggBot stores the stable Discord user ID, current Discord
username and server display name alongside the Minecraft IGN. Admins with **Manage
Roles** can query either side of the association in the admin channel with
`!whois <Discord name, mention, ID, or Minecraft IGN>`. Approval and denial remove the
thumb voting reactions from the retained application message.

Each result labels the Minecraft IGN, Discord username, server display name, account
mention, stable Discord user ID, source application ID, and linked timestamp.

Members without an application-created association may create a one-time,
self-reported association through `!hours`. `!whois` labels these as **Self-reported
through !hours** and application-created links as **Approved application**.
Self-reporting never grants whitelist access or Discord roles.

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
