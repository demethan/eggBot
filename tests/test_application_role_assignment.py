import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from application_service import DecisionResult, ServerWhitelistOutcome
from support_commands_cog import MemberRoleAssignmentError, SupportCommandsCog


def role(role_id=20, *, position=5, managed=False):
    return SimpleNamespace(id=role_id, position=position, managed=managed)


def member(*roles, administrator=False, manage_roles=False, top_position=10):
    return SimpleNamespace(
        roles=list(roles),
        guild_permissions=SimpleNamespace(
            administrator=administrator, manage_roles=manage_roles
        ),
        top_role=SimpleNamespace(position=top_position),
        add_roles=AsyncMock(),
    )


class ApplicationRoleAssignmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        runtime = Mock()
        runtime.require_int.return_value = 20
        self.client = SimpleNamespace(runtime_config=runtime, user=SimpleNamespace(id=1))
        self.cog = SupportCommandsCog(self.client)
        self.application = SimpleNamespace(id=7, discord_user_id=123)

    async def test_assigns_role_and_verifies_refreshed_member(self):
        member_role = role()
        before = member()
        after = member(member_role)
        guild = SimpleNamespace(
            get_role=Mock(return_value=member_role),
            me=member(manage_roles=True),
            fetch_member=AsyncMock(side_effect=[before, after]),
        )

        assigned = await self.cog._assign_member_role(guild, self.application)

        before.add_roles.assert_awaited_once_with(
            member_role, reason="Application #7 approved"
        )
        self.assertIs(assigned, after)

    async def test_rejects_role_above_eggbot(self):
        member_role = role(position=10)
        guild = SimpleNamespace(
            get_role=Mock(return_value=member_role),
            me=member(manage_roles=True, top_position=10),
        )

        with self.assertRaisesRegex(MemberRoleAssignmentError, "highest role"):
            await self.cog._assign_member_role(guild, self.application)

    async def test_administrator_still_cannot_assign_role_above_eggbot(self):
        member_role = role(position=11)
        guild = SimpleNamespace(
            get_role=Mock(return_value=member_role),
            me=member(
                administrator=True, manage_roles=True, top_position=10
            ),
        )

        with self.assertRaisesRegex(MemberRoleAssignmentError, "highest role"):
            await self.cog._assign_member_role(guild, self.application)

    async def test_rejects_missing_manage_roles_permission(self):
        guild = SimpleNamespace(get_role=Mock(return_value=role()), me=member())

        with self.assertRaisesRegex(MemberRoleAssignmentError, "Manage Roles"):
            await self.cog._assign_member_role(guild, self.application)

    async def test_existing_role_is_idempotent(self):
        member_role = role()
        applicant = member(member_role)
        guild = SimpleNamespace(
            get_role=Mock(return_value=member_role),
            me=member(manage_roles=True),
            fetch_member=AsyncMock(return_value=applicant),
        )

        assigned = await self.cog._assign_member_role(guild, self.application)

        applicant.add_roles.assert_not_awaited()
        self.assertIs(assigned, applicant)

    async def test_partial_whitelist_approval_still_assigns_member_role(self):
        application = SimpleNamespace(
            id=7,
            discord_user_id=123,
            status="partial_failure",
            minecraft_name="Player",
            over_18=True,
            sponsor_type=None,
            sponsor_identifier=None,
            source="Friend",
            submitted_at="2026-08-24T00:00:00+00:00",
        )
        result = DecisionResult(
            "partial_failure",
            application,
            {"BACON": ServerWhitelistOutcome("failed", "connection_error")},
        )
        service = SimpleNamespace(
            get_by_admin_message=Mock(return_value=application),
            approve=AsyncMock(return_value=result),
        )
        self.cog.application_service = service
        self.cog._handle_followup_reaction = AsyncMock(return_value=False)
        assigned = member(role())
        self.cog._assign_member_role = AsyncMock(return_value=assigned)
        message = SimpleNamespace(
            id=55,
            channel=SimpleNamespace(id=10),
            guild=SimpleNamespace(fetch_member=AsyncMock(return_value=assigned)),
            edit=AsyncMock(),
        )
        reaction = SimpleNamespace(message=message, emoji="👍")
        reviewer = SimpleNamespace(
            id=999,
            bot=False,
            guild_permissions=SimpleNamespace(
                administrator=True, manage_roles=True
            ),
        )
        self.client.runtime_config.require_int.side_effect = lambda key: {
            "adminChannelID": 10,
            "memberRoleID": 20,
        }[key]

        await self.cog.on_reaction_add(reaction, reviewer)

        self.cog._assign_member_role.assert_awaited_once_with(
            message.guild, application
        )
        message.edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
