import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from commands_cog import CommandsCog


def make_context(*, admin=False, channel_id=10, guild=True):
    author = SimpleNamespace(
        id=123,
        name="discord_user",
        display_name="Display Name",
        guild_permissions=SimpleNamespace(
            administrator=admin,
            manage_roles=admin,
        ),
        send=AsyncMock(),
    )
    return SimpleNamespace(
        guild=SimpleNamespace() if guild else None,
        author=author,
        channel=SimpleNamespace(id=channel_id),
        send=AsyncMock(),
        message=SimpleNamespace(add_reaction=AsyncMock()),
    )


class HoursCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tracking = Mock()
        self.tracking.weekly_hours.return_value = (
            datetime(2026, 8, 3),
            datetime(2026, 8, 4),
            [],
        )
        self.runtime = Mock()
        self.runtime.require_int.return_value = 10
        self.client = SimpleNamespace(
            tracking_service=self.tracking,
            runtime_config=self.runtime,
        )
        self.applications = Mock()
        self.cog = CommandsCog(self.client, self.applications)

    async def test_member_hours_uses_only_discord_association_and_dms_result(self):
        link = SimpleNamespace(minecraft_name="OwnIGN")
        self.applications.find_player_links.return_value = [link]
        ctx = make_context(channel_id=99)

        await self.cog.hours.callback(self.cog, ctx, minecraft_name=None)

        self.applications.find_player_links.assert_called_once_with("123")
        self.tracking.weekly_hours.assert_called_once_with("OwnIGN")
        ctx.author.send.assert_awaited_once()
        ctx.send.assert_not_awaited()

    async def test_member_hours_works_in_dm_without_guild_permissions(self):
        link = SimpleNamespace(minecraft_name="OwnIGN")
        self.applications.find_player_links.return_value = [link]
        ctx = make_context(channel_id=99, guild=False)
        del ctx.author.guild_permissions

        await self.cog.hours.callback(self.cog, ctx, minecraft_name=None)

        self.applications.find_player_links.assert_called_once_with("123")
        self.tracking.weekly_hours.assert_called_once_with("OwnIGN")
        ctx.author.send.assert_awaited_once()

    async def test_member_cannot_lookup_supplied_ign(self):
        ctx = make_context(channel_id=99)

        await self.cog.hours.callback(self.cog, ctx, minecraft_name="SomeoneElse")

        self.applications.find_player_links.assert_not_called()
        self.tracking.weekly_hours.assert_not_called()
        warning = ctx.author.send.await_args.args[0]
        self.assertIn("cannot look up an IGN directly", warning)

    async def test_admin_channel_retains_arbitrary_ign_lookup(self):
        ctx = make_context(admin=True, channel_id=10)

        await self.cog.hours.callback(self.cog, ctx, minecraft_name="SomeoneElse")

        self.tracking.weekly_hours.assert_called_once_with("SomeoneElse")
        ctx.send.assert_awaited_once()
        ctx.author.send.assert_not_awaited()

    async def test_unlinked_member_enters_private_association_flow(self):
        self.applications.find_player_links.return_value = []
        link = SimpleNamespace(minecraft_name="NewIGN")
        self.cog._request_hours_association = AsyncMock(return_value=link)
        ctx = make_context(channel_id=99)

        await self.cog.hours.callback(self.cog, ctx, minecraft_name=None)

        self.cog._request_hours_association.assert_awaited_once_with(ctx)
        self.tracking.weekly_hours.assert_called_once_with("NewIGN")


if __name__ == "__main__":
    unittest.main()
