# Command error feedback

EggBot reacts to a command message with a category-specific icon and sends a short
explanation when appropriate:

- ❓ unknown command
- ⚠️ missing required argument
- 🚫 invalid argument
- 🔒 missing permission or command used in the wrong channel
- ⏳ command cooldown
- ❌ unexpected command failure

Usage errors direct the user to `!help` or `!help <command>`.
