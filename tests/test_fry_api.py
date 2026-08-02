import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from eggbot_db.repositories import Server, ServerCapabilities
from fry_api import FryApiClient, FryErrorCode, FryResult


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload

    async def json(self, content_type=None):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeRequest:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return FakeRequest(next(self.outcomes))

    async def close(self):
        self.closed = True


def server(
    *,
    server_id=1,
    name="BACON",
    token="cached-token",
    password="api-password",
    capabilities=None,
):
    return Server(
        id=server_id,
        name=name,
        endpoint=f"https://{name.lower()}.example",
        api_user="api-user",
        api_password=password,
        api_token=token,
        enabled=True,
        capabilities=capabilities or ServerCapabilities(),
    )


class FryApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_uses_cached_token_and_shared_session(self):
        session = FakeSession(
            [
                FakeResponse(200, {"data": {"name": "BACON"}}),
                FakeResponse(200, {"data": {"name": "BACON", "status": "RUNNING"}}),
            ]
        )
        client = FryApiClient(session=session)

        first = await client.get_metadata(server())
        second = await client.get_metadata(server())

        self.assertTrue(first.ok)
        self.assertEqual(second.value["status"], "RUNNING")
        self.assertEqual(len(session.requests), 2)
        self.assertEqual(
            session.requests[0][2]["headers"]["Authorization"],
            "Bearer cached-token",
        )

    async def test_expired_token_refreshes_once_and_invokes_callback(self):
        callback = AsyncMock()
        session = FakeSession(
            [
                FakeResponse(401, {"error": "Unauthorized"}),
                FakeResponse(200, {"data": {"token": "fresh-token"}}),
                FakeResponse(200, {"data": {"name": "BACON"}}),
            ]
        )
        client = FryApiClient(session=session, on_token_refreshed=callback)

        result = await client.get_metadata(server())

        self.assertTrue(result.ok)
        self.assertEqual([request[0] for request in session.requests], ["GET", "POST", "GET"])
        self.assertEqual(
            session.requests[-1][2]["headers"]["Authorization"],
            "Bearer fresh-token",
        )
        callback.assert_awaited_once()

    async def test_repeated_auth_failure_is_bounded(self):
        session = FakeSession(
            [
                FakeResponse(401),
                FakeResponse(200, {"data": {"token": "fresh-token"}}),
                FakeResponse(401),
            ]
        )
        result = await FryApiClient(session=session).get_metadata(server())
        self.assertEqual(result.error.code, FryErrorCode.AUTHENTICATION_FAILED)
        self.assertEqual(len(session.requests), 3)

    async def test_token_persistence_failure_is_structured(self):
        def fail(server, token):
            raise RuntimeError("database unavailable")

        session = FakeSession([FakeResponse(200, {"data": {"token": "fresh-token"}})])
        client = FryApiClient(session=session, on_token_refreshed=fail)
        result = await client.authenticate(server(token=None))
        self.assertEqual(result.error.code, FryErrorCode.INTERNAL_ERROR)

    async def test_capability_flags_block_unsupported_and_denied_calls(self):
        unsupported_session = FakeSession([])
        unsupported = server(
            capabilities=ServerCapabilities(metadata_read="unsupported")
        )
        result = await FryApiClient(session=unsupported_session).get_metadata(unsupported)
        self.assertEqual(result.error.code, FryErrorCode.UNSUPPORTED)
        self.assertEqual(unsupported_session.requests, [])

        denied_session = FakeSession([])
        denied = server(
            capabilities=ServerCapabilities(whitelist_write="permission_denied")
        )
        result = await FryApiClient(session=denied_session).whitelist_add(denied, "Player")
        self.assertEqual(result.error.code, FryErrorCode.PERMISSION_DENIED)
        self.assertEqual(denied_session.requests, [])

    async def test_whitelist_check_distinguishes_missing_player(self):
        session = FakeSession([FakeResponse(404, {"error": "Not Found"})])
        result = await FryApiClient(session=session).whitelist_contains(
            server(), "Player Name/Unsafe"
        )
        self.assertTrue(result.ok)
        self.assertIs(result.value, False)
        self.assertTrue(session.requests[0][1].endswith("Player%20Name%2FUnsafe/"))

    async def test_status_errors_are_structured(self):
        expectations = {
            400: FryErrorCode.INVALID_REQUEST,
            403: FryErrorCode.PERMISSION_DENIED,
            429: FryErrorCode.RATE_LIMITED,
            500: FryErrorCode.SERVER_ERROR,
        }
        for status, expected in expectations.items():
            with self.subTest(status=status):
                outcomes = [FakeResponse(status)]
                if status == 403:
                    outcomes.extend(
                        [
                            FakeResponse(200, {"data": {"token": "new"}}),
                            FakeResponse(403),
                        ]
                    )
                session = FakeSession(outcomes)
                result = await FryApiClient(
                    session=session,
                    max_retries=0,
                ).whitelist_add(server(), "Player")
                self.assertEqual(result.error.code, expected)

    async def test_timeout_and_connection_errors_are_distinct(self):
        timeout = await FryApiClient(
            session=FakeSession([asyncio.TimeoutError()]), max_retries=0
        ).get_metadata(server())
        connection = await FryApiClient(
            session=FakeSession([aiohttp.ClientConnectionError("offline")]),
            max_retries=0,
        ).get_metadata(server())
        self.assertEqual(timeout.error.code, FryErrorCode.TIMEOUT)
        self.assertEqual(connection.error.code, FryErrorCode.CONNECTION_ERROR)

    @patch("fry_api.asyncio.sleep", new_callable=AsyncMock)
    async def test_safe_reads_retry_with_a_bound(self, sleep):
        session = FakeSession(
            [
                FakeResponse(500),
                FakeResponse(500),
                FakeResponse(200, {"data": {"name": "BACON"}}),
            ]
        )
        result = await FryApiClient(
            session=session,
            max_retries=2,
            retry_delay=0,
        ).get_metadata(server())
        self.assertTrue(result.ok)
        self.assertEqual(len(session.requests), 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_invalid_json_response_is_rejected(self):
        session = FakeSession([FakeResponse(200, ValueError("not json"))])
        result = await FryApiClient(session=session).get_metadata(server())
        self.assertEqual(result.error.code, FryErrorCode.INVALID_RESPONSE)

    async def test_batch_failure_does_not_hide_other_servers(self):
        client = FryApiClient(session=FakeSession([]))

        async def add(current_server, name):
            if current_server.name == "BAGEL":
                raise RuntimeError("unexpected")
            if current_server.name == "DONUT":
                return FryResult.failure(FryErrorCode.CONNECTION_ERROR)
            return FryResult.success(f"{name} added")

        client.whitelist_add = add
        results = await client.whitelist_add_many(
            [
                server(server_id=1, name="BACON"),
                server(server_id=2, name="BAGEL"),
                server(server_id=3, name="DONUT"),
            ],
            "Player",
        )
        self.assertTrue(results["BACON"].ok)
        self.assertEqual(results["BAGEL"].error.code, FryErrorCode.INTERNAL_ERROR)
        self.assertEqual(results["DONUT"].error.code, FryErrorCode.CONNECTION_ERROR)

    async def test_server_repr_excludes_credentials(self):
        representation = repr(server())
        self.assertNotIn("api-password", representation)
        self.assertNotIn("cached-token", representation)

    async def test_client_only_closes_owned_session(self):
        injected = FakeSession([])
        client = FryApiClient(session=injected)
        await client.close()
        self.assertFalse(injected.closed)


if __name__ == "__main__":
    unittest.main()
