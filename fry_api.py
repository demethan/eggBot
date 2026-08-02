"""Shared asynchronous client for FryingPan2 APIs."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Generic, Iterable, Optional, TypeVar
from urllib.parse import quote

import aiohttp

from eggbot_db.repositories import Server


T = TypeVar("T")
TokenCallback = Callable[[Server, str], Optional[Awaitable[None]]]


class FryErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED = "unsupported"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class FryError:
    code: FryErrorCode
    status: Optional[int] = None
    retryable: bool = False
    message: str = ""


@dataclass(frozen=True)
class FryResult(Generic[T]):
    value: Optional[T] = None
    error: Optional[FryError] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def success(cls, value: T) -> "FryResult[T]":
        return cls(value=value)

    @classmethod
    def failure(
        cls,
        code: FryErrorCode,
        *,
        status: Optional[int] = None,
        retryable: bool = False,
        message: str = "",
    ) -> "FryResult[T]":
        return cls(
            error=FryError(
                code=code,
                status=status,
                retryable=retryable,
                message=message,
            )
        )


@dataclass(frozen=True)
class HttpOutcome:
    status: Optional[int]
    payload: Any = None
    error: Optional[FryError] = None


class FryApiClient:
    def __init__(
        self,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        on_token_refreshed: Optional[TokenCallback] = None,
    ):
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_retries = max(0, max_retries)
        self._retry_delay = max(0.0, retry_delay)
        self._on_token_refreshed = on_token_refreshed
        self._tokens: Dict[int, str] = {}

    async def __aenter__(self) -> "FryApiClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._owns_session = True

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _session_or_start(self) -> aiohttp.ClientSession:
        await self.start()
        return self._session

    @staticmethod
    def _endpoint(server: Server, path: str) -> str:
        return f"{server.endpoint.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _capability_error(server: Server, capability: str) -> Optional[FryResult[Any]]:
        status = server.capabilities.status_for(capability)
        if status == "unsupported":
            return FryResult.failure(FryErrorCode.UNSUPPORTED)
        if status in ("permission_denied", "denied"):
            return FryResult.failure(FryErrorCode.PERMISSION_DENIED, status=403)
        return None

    async def _perform(
        self,
        method: str,
        url: str,
        *,
        data: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        retry_safe: bool = False,
    ) -> HttpOutcome:
        session = await self._session_or_start()
        attempts = self._max_retries + 1 if retry_safe else 1
        for attempt in range(attempts):
            try:
                async with session.request(
                    method,
                    url,
                    data=data,
                    headers=headers,
                ) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        payload = None
                    outcome = HttpOutcome(status=response.status, payload=payload)
            except asyncio.TimeoutError:
                outcome = HttpOutcome(
                    status=None,
                    error=FryError(FryErrorCode.TIMEOUT, retryable=True),
                )
            except aiohttp.ClientError:
                outcome = HttpOutcome(
                    status=None,
                    error=FryError(FryErrorCode.CONNECTION_ERROR, retryable=True),
                )

            should_retry = (
                retry_safe
                and attempt + 1 < attempts
                and (
                    (outcome.error is not None and outcome.error.retryable)
                    or outcome.status == 429
                    or (outcome.status is not None and outcome.status >= 500)
                )
            )
            if not should_retry:
                return outcome
            await asyncio.sleep(self._retry_delay * (attempt + 1))
        return outcome

    async def authenticate(self, server: Server, *, force: bool = False) -> FryResult[str]:
        if not force:
            token = self._tokens.get(server.id) or server.api_token
            if token:
                self._tokens[server.id] = token
                return FryResult.success(token)

        if not server.api_user or not server.api_password:
            return FryResult.failure(
                FryErrorCode.AUTHENTICATION_FAILED,
                message="Fry API credentials are not configured",
            )

        outcome = await self._perform(
            "POST",
            self._endpoint(server, "/v1/token/"),
            data={"id": server.api_user, "password": server.api_password},
            retry_safe=True,
        )
        if outcome.error:
            return FryResult(error=outcome.error)
        if outcome.status in (401, 403):
            return FryResult.failure(
                FryErrorCode.AUTHENTICATION_FAILED,
                status=outcome.status,
            )
        if outcome.status != 200:
            return self._failure_for_status(outcome.status)
        try:
            token = outcome.payload["data"]["token"]
            if not isinstance(token, str) or not token:
                raise TypeError
        except (KeyError, TypeError):
            return FryResult.failure(FryErrorCode.INVALID_RESPONSE)

        self._tokens[server.id] = token
        if self._on_token_refreshed:
            try:
                callback_result = self._on_token_refreshed(server, token)
                if inspect.isawaitable(callback_result):
                    await callback_result
            except Exception:
                return FryResult.failure(FryErrorCode.INTERNAL_ERROR)
        return FryResult.success(token)

    @staticmethod
    def _failure_for_status(status: Optional[int]) -> FryResult[Any]:
        if status == 400:
            return FryResult.failure(FryErrorCode.INVALID_REQUEST, status=status)
        if status == 401:
            return FryResult.failure(FryErrorCode.AUTHENTICATION_FAILED, status=status)
        if status == 403:
            return FryResult.failure(FryErrorCode.PERMISSION_DENIED, status=status)
        if status == 404:
            return FryResult.failure(FryErrorCode.NOT_FOUND, status=status)
        if status == 429:
            return FryResult.failure(
                FryErrorCode.RATE_LIMITED,
                status=status,
                retryable=True,
            )
        if status is not None and status >= 500:
            return FryResult.failure(
                FryErrorCode.SERVER_ERROR,
                status=status,
                retryable=True,
            )
        return FryResult.failure(FryErrorCode.INVALID_RESPONSE, status=status)

    async def _authorized_request(
        self,
        server: Server,
        method: str,
        path: str,
        *,
        data: Optional[Dict[str, str]] = None,
        capability: str,
        retry_safe: bool = False,
    ) -> FryResult[Any]:
        capability_error = self._capability_error(server, capability)
        if capability_error:
            return capability_error

        authentication = await self.authenticate(server)
        if not authentication.ok:
            return FryResult(error=authentication.error)

        refreshed = False
        while True:
            outcome = await self._perform(
                method,
                self._endpoint(server, path),
                data=data,
                headers={"Authorization": f"Bearer {authentication.value}"},
                retry_safe=retry_safe,
            )
            if outcome.error:
                return FryResult(error=outcome.error)
            if outcome.status in (401, 403) and not refreshed:
                refreshed = True
                authentication = await self.authenticate(server, force=True)
                if not authentication.ok:
                    return FryResult(error=authentication.error)
                continue
            if outcome.status != 200:
                return self._failure_for_status(outcome.status)
            return FryResult.success(outcome.payload)

    async def get_metadata(self, server: Server) -> FryResult[Dict[str, Any]]:
        result = await self._authorized_request(
            server,
            "GET",
            "/v1/meta/",
            capability="metadata_read",
            retry_safe=True,
        )
        if not result.ok:
            return result
        try:
            metadata = result.value["data"]
            if not isinstance(metadata, dict):
                raise TypeError
        except (KeyError, TypeError):
            return FryResult.failure(FryErrorCode.INVALID_RESPONSE)
        return FryResult.success(metadata)

    async def whitelist_contains(self, server: Server, name: str) -> FryResult[bool]:
        result = await self._authorized_request(
            server,
            "GET",
            f"/v1/whitelist/{quote(name, safe='')}/",
            capability="whitelist_read",
            retry_safe=True,
        )
        if result.error and result.error.code == FryErrorCode.NOT_FOUND:
            return FryResult.success(False)
        if not result.ok:
            return result
        return FryResult.success(True)

    async def whitelist_add(self, server: Server, name: str) -> FryResult[str]:
        if not name.strip():
            return FryResult.failure(FryErrorCode.INVALID_REQUEST)
        result = await self._authorized_request(
            server,
            "POST",
            "/v1/whitelist/",
            data={"name": name.strip()},
            capability="whitelist_write",
        )
        return self._message_result(result)

    async def whitelist_remove(self, server: Server, name: str) -> FryResult[str]:
        if not name.strip():
            return FryResult.failure(FryErrorCode.INVALID_REQUEST)
        result = await self._authorized_request(
            server,
            "DELETE",
            f"/v1/whitelist/{quote(name.strip(), safe='')}/",
            capability="whitelist_write",
        )
        return self._message_result(result)

    async def whitelist_reload(self, server: Server) -> FryResult[str]:
        result = await self._authorized_request(
            server,
            "PATCH",
            "/v1/whitelist/",
            data={},
            capability="whitelist_write",
        )
        return self._message_result(result)

    @staticmethod
    def _message_result(result: FryResult[Any]) -> FryResult[str]:
        if not result.ok:
            return FryResult(error=result.error)
        message = result.value.get("message") if isinstance(result.value, dict) else None
        if not isinstance(message, str):
            return FryResult.failure(FryErrorCode.INVALID_RESPONSE)
        return FryResult.success(message)

    async def whitelist_add_many(
        self,
        servers: Iterable[Server],
        name: str,
    ) -> Dict[str, FryResult[str]]:
        server_list = list(servers)
        outcomes = await asyncio.gather(
            *(self.whitelist_add(server, name) for server in server_list),
            return_exceptions=True,
        )
        results = {}
        for server, outcome in zip(server_list, outcomes):
            if isinstance(outcome, Exception):
                results[server.name] = FryResult.failure(FryErrorCode.INTERNAL_ERROR)
            else:
                results[server.name] = outcome
        return results
