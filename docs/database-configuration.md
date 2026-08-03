# Database-backed configuration commands

EggBot's legacy configuration commands now persist changes in SQLite. The JSON
data file remains an import source only; these commands do not write to it.

Admin commands:

- `!updateids <key> <Discord ID>` updates a channel or role ID.
- `!setsource <Discord ID> <server>` authorizes a Fry bot or webhook as the
  notification source for an enabled server.
- `!set <applyUrl|joinMessage> <text>` updates concierge settings.
- `!add <Fry URL> <API user>` requests the API password by DM, authenticates
  with Fry, discovers the server name, encrypts its credentials, and enables it.
- `!remove <server>` disables a server after a ✅ confirmation. Player sessions,
  pack installations, and statistics are retained.
- `!set_schedule <server> <HH:MM:SS> <frequency> <timezone>` stores a reboot
  schedule. The time is interpreted in the named IANA timezone and persisted in
  UTC along with the display timezone.
- `!getmeta <server>` reads metadata for an enabled, database-backed server.

At startup, EggBot hydrates the remaining legacy runtime readers from SQLite.
This compatibility layer can be removed once those readers have independently
moved to repository/service APIs.
