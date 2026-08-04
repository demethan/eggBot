import unittest
from types import SimpleNamespace

from help_command import EggBotHelpCommand


class HelpCommandTests(unittest.TestCase):
    def test_help_explicitly_identifies_command_prefix(self):
        help_command = EggBotHelpCommand()
        help_command.context = SimpleNamespace(
            clean_prefix="!",
            invoked_with="help",
            command=SimpleNamespace(qualified_name="help"),
        )

        note = help_command.get_ending_note()

        self.assertIn("All commands must start with `!`", note)
        self.assertIn("!help command", note)


if __name__ == "__main__":
    unittest.main()
