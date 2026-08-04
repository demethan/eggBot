"""EggBot's user-facing Discord help command."""

from discord.ext import commands


class EggBotHelpCommand(commands.DefaultHelpCommand):
    def get_ending_note(self):
        return (
            f"All commands must start with `{self.context.clean_prefix}`.\n"
            f"{super().get_ending_note()}"
        )
