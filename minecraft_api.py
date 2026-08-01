"""Asynchronous client helpers for Mojang's Minecraft profile API."""

import asyncio
import re
from typing import Optional

import aiohttp


PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{username}"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,16}$")


async def validate_minecraft_username(
    session: aiohttp.ClientSession,
    username: str,
    *,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> Optional[bool]:
    """Return True/False for a valid/missing IGN, or None on API failure."""
    username = username.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        return False

    for attempt in range(max_retries):
        try:
            async with session.get(PROFILE_URL.format(username=username)) as response:
                if response.status == 200:
                    return True
                if response.status in (204, 404):
                    return False
                if response.status != 429 and response.status < 500:
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass

        if attempt + 1 < max_retries:
            await asyncio.sleep(retry_delay * (attempt + 1))

    return None
