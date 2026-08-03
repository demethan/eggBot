import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from admin_commands_cog import admin_allowed
from commands_cog import member_allowed


def context(*, guild=True, channel_id=10, admin_channel_id=10, member=False,
            administrator=False, manage_roles=False):
    runtime = Mock()
    runtime.require_int.side_effect = lambda key: {
        "adminChannelID": admin_channel_id,
        "memberRoleID": 20,
    }[key]
    permissions = SimpleNamespace(
        administrator=administrator,
        manage_roles=manage_roles,
    )
    roles = [SimpleNamespace(id=20)] if member else []
    return SimpleNamespace(
        guild=SimpleNamespace() if guild else None,
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(guild_permissions=permissions, roles=roles),
        bot=SimpleNamespace(runtime_config=runtime),
    )


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_commands_require_member_role_but_allow_admin(self):
        self.assertTrue(await member_allowed(context(member=True)))
        self.assertTrue(await member_allowed(context(administrator=True)))
        self.assertFalse(await member_allowed(context()))
        self.assertFalse(await member_allowed(context(guild=False)))

    async def test_admin_commands_require_permission_and_correct_channel(self):
        self.assertTrue(await admin_allowed(context(manage_roles=True)))
        self.assertTrue(await admin_allowed(context(administrator=True)))
        self.assertFalse(await admin_allowed(context()))
        self.assertFalse(
            await admin_allowed(context(manage_roles=True, channel_id=11))
        )
        self.assertFalse(await admin_allowed(context(guild=False, manage_roles=True)))


if __name__ == "__main__":
    unittest.main()
