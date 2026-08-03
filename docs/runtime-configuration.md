# Runtime configuration

EggBot reads mutable configuration from SQLite through `RuntimeConfig`. The Discord
token remains in the protected, untracked `config.json`; `data.json` is not read or
written during normal operation.

## Fry notification sources

Messages in the configured general channel are accepted only when their Discord bot
or webhook ID is mapped to an enabled Fry server. Administrators configure a source
in the admin channel:

```text
!setsource <Discord bot or webhook ID> <server name>
```

For webhook messages EggBot validates `message.webhook_id`; for bot messages it
validates `message.author.id`. An unmapped bot is ignored and logged by numeric ID.
Display names are never trusted as authentication.

Before deploying strict authentication, map every production Fry source and verify
the resulting `serverNotificationSources` setting without printing unrelated setting
values or credentials.

## Authorization

`!s`, `!o`, `!ls`, `!c`, `!schedule`, and `!roll` require the configured member
role (server administrators are also accepted). Application intake remains available
to non-members in the support channel. Every command in the admin cog requires both
the configured admin channel and either Manage Roles or Administrator permission.
