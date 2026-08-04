import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from admin_commands_cog import admin_allowed
from commands_cog import member_allowed


def context(*, guild=True, channel_id=10, admin_channel_id=10, member=False,
            administrator=False, manage_roles=False, mutual_member=None):
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
    author = SimpleNamespace(
        id=123,
        guild_permissions=permissions,
        roles=roles,
    )
    mutual_guild = SimpleNamespace(
        get_member=Mock(return_value=mutual_member),
        fetch_member=AsyncMock(side_effect=discord.NotFound(Mock(), "not found")),
    )
    return SimpleNamespace(
        guild=SimpleNamespace() if guild else None,
        channel=SimpleNamespace(id=channel_id),
        author=author,
        bot=SimpleNamespace(runtime_config=runtime, guilds=[mutual_guild]),
    )


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_commands_require_member_role_but_allow_admin(self):
        self.assertTrue(await member_allowed(context(member=True)))
        self.assertTrue(await member_allowed(context(administrator=True)))
        self.assertFalse(await member_allowed(context()))

    async def test_member_commands_allow_dm_from_member_in_mutual_guild(self):
        member = SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=False),
            roles=[SimpleNamespace(id=20)],
        )
        self.assertTrue(
            await member_allowed(context(guild=False, mutual_member=member))
        )

    async def test_member_commands_reject_dm_from_non_member(self):
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
