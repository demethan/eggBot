import unittest

from discord.ext import commands

from command_errors import reaction_for_command_error


class CommandErrorReactionTests(unittest.TestCase):
    def test_error_categories_have_relevant_reactions(self):
        parameter = commands.Parameter(name="ign", kind=1)
        cases = [
            (commands.CommandNotFound("missing"), "❓"),
            (commands.MissingRequiredArgument(parameter), "⚠️"),
            (commands.BadArgument("bad"), "🚫"),
            (commands.CheckFailure("denied"), "🔒"),
            (RuntimeError("unexpected"), "❌"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(reaction_for_command_error(error), expected)


if __name__ == "__main__":
    unittest.main()
