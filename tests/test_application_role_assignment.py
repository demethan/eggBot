import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


if __name__ == "__main__":
    unittest.main()
