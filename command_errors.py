"""User-facing classification for Discord command errors."""

from discord.ext import commands


def reaction_for_command_error(error: Exception) -> str:
    error = getattr(error, "original", error)
    if isinstance(error, commands.CommandNotFound):
        return "❓"
    if isinstance(error, commands.MissingRequiredArgument):
        return "⚠️"
    if isinstance(error, commands.CommandOnCooldown):
        return "⏳"
    if isinstance(error, commands.CheckFailure):
        return "🔒"
    if isinstance(error, commands.UserInputError):
        return "🚫"
    return "❌"
