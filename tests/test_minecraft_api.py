import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from minecraft_api import validate_minecraft_username


class FakeResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeRequest:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return FakeResponse(self.outcome)

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return FakeRequest(next(self.outcomes))


class MinecraftUsernameValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_username(self):
        session = FakeSession([200])
        result = await validate_minecraft_username(session, "Demethan")
        self.assertIs(result, True)
        self.assertEqual(len(session.urls), 1)

    async def test_missing_username(self):
        result = await validate_minecraft_username(FakeSession([404]), "NoSuchUser")
        self.assertIs(result, False)

    async def test_rejects_invalid_format_without_request(self):
        session = FakeSession([])
        result = await validate_minecraft_username(session, "invalid name!")
        self.assertIs(result, False)
        self.assertEqual(session.urls, [])

    @patch("minecraft_api.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_rate_limit_then_succeeds(self, sleep):
        result = await validate_minecraft_username(
            FakeSession([429, 200]),
            "Demethan",
            retry_delay=0,
        )
        self.assertIs(result, True)
        sleep.assert_awaited_once()

    @patch("minecraft_api.asyncio.sleep", new_callable=AsyncMock)
    async def test_returns_unknown_after_connection_failures(self, sleep):
        failure = aiohttp.ClientConnectionError("offline")
        result = await validate_minecraft_username(
            FakeSession([failure, failure, failure]),
            "Demethan",
            retry_delay=0,
        )
        self.assertIsNone(result)
        self.assertEqual(sleep.await_count, 2)

    async def test_non_retryable_api_failure_is_unknown(self):
        result = await validate_minecraft_username(FakeSession([403]), "Demethan")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
