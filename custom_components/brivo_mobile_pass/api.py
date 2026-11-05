"""Brivo Mobile Pass HTTP client, independent of Home Assistant."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import quote

import aiohttp

AUTH_URL = "https://auth.brivo.com/oauth/token"
API_URL = "https://pi.brivo.com/api"

_LOGGER = logging.getLogger(__name__)
ErrorCategory = Literal[
    "auth",
    "connection",
    "timeout",
    "invalid_response",
    "credentials_persistence",
    "http",
    "no_access_points",
    "access_point_unavailable",
]


class BrivoError(Exception):
    """An error safe to show without exposing credentials."""

    category: ErrorCategory = "invalid_response"

    def __init__(self, message: str, *, category: ErrorCategory | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category


class BrivoAuthError(BrivoError):
    """Credentials require reauthentication."""

    category: ErrorCategory = "auth"


class HttpFailure(BrivoError):
    """An HTTP error with no sensitive response or URL."""

    category: ErrorCategory = "http"

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"Brivo returned HTTP {status}")


class BrivoClient:
    """Serialize API calls and persist rotated tokens before using them."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        application: dict[str, Any],
        credential: str,
        access_points: list[dict[str, Any]],
        tokens: dict[str, Any],
        persist: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.session = session
        self.application = application
        self.credential = credential
        self.access_points = access_points
        self.tokens = dict(tokens)
        self.persist = persist
        self.lock = asyncio.Lock()
        self._dirty = False
        self._closing = False
        self._last_successful_request_at: float | None = None
        self._last_error: str | None = None
        self._last_error_at: float | None = None

    @property
    def diagnostics(self) -> dict[str, float | str | None]:
        """Return a copy of allowlisted, in-memory telemetry (None until known).

        Timestamps are UTC Unix seconds. Request success means an HTTP <400
        response decoded as JSON (or empty), including token refresh requests;
        later schema validation or credential persistence can still fail.
        Error categories describe the last completed high-level operation and
        clear only when a whole high-level operation succeeds.
        """
        return {
            "last_successful_request_at": self._last_successful_request_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        """Serialize operations; only full success clears the previous error.

        Intermediate HTTP/token-refresh successes update the request timestamp,
        but never mask a failed or still-running high-level operation. Cancelled
        operations leave the last completed operation's error unchanged.
        """
        async with self.lock:
            if self._closing:
                raise BrivoError("Brivo client is unloading; try again after reload")
            try:
                yield
            except BrivoError as exc:
                self._last_error = exc.category
                self._last_error_at = time.time()
                _LOGGER.debug("Brivo operation failed (%s)", exc.category)
                raise
            else:
                self._last_error = None
                self._last_error_at = None

    async def async_close(self) -> None:
        """Reject queued work, drain the lock holder, and durably save rotation.

        The closing flag is set before yielding, so even earlier lock waiters
        cannot start HTTP requests. The owner may reopen if unloading fails.
        """
        self._closing = True
        async with self.lock:
            await self._save_tokens()

    def reopen(self) -> None:
        """Resume the same runtime only when its unload did not complete."""
        self._closing = False

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            async with self.session.request(
                method, url, timeout=aiohttp.ClientTimeout(total=25), **kwargs
            ) as response:
                if response.status >= 400:
                    raise HttpFailure(response.status)
                raw = await response.read()
                result = json.loads(raw) if raw else None
                self._last_successful_request_at = time.time()
                return result
        except TimeoutError:
            raise BrivoError(
                "Brivo connection timed out; command was not retried", category="timeout"
            ) from None
        except aiohttp.ClientError:
            raise BrivoError(
                "Brivo connection failed; command was not retried", category="connection"
            ) from None
        except ValueError, UnicodeError:
            raise BrivoError("Brivo returned an invalid response") from None

    def _basic(self) -> str:
        client_id = self.application["client_id"]
        secret = self.application["client_secret"]
        refresh = self.tokens.get("refresh_token", "")
        try:
            part = refresh.split(".")[1]
            token_client = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get(
                "client_id"
            )
        except ValueError, IndexError, TypeError:
            token_client = None
        if token_client and token_client != client_id:
            secret = self.application.get("client_secrets", {}).get(token_client)
            if not secret:
                raise BrivoAuthError("Refresh token belongs to an unconfigured application")
            client_id = token_client
        return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()

    async def _save_tokens(self) -> None:
        if self._dirty:
            try:
                await self.persist(dict(self.tokens))
            except Exception:
                raise BrivoError(
                    "Could not save credentials; unlock was not sent",
                    category="credentials_persistence",
                ) from None
            self._dirty = False

    async def _accept_tokens(self, result: Any) -> None:
        if not isinstance(result, dict) or not result.get("access_token"):
            raise BrivoError("Brivo returned no access token")
        try:
            expires_in = float(result.get("expires_in", 300))
        except TypeError, ValueError, OverflowError:
            raise BrivoError("Brivo returned an invalid token response") from None
        if (
            not isinstance(result["access_token"], str)
            or (
                result.get("refresh_token") is not None
                and not isinstance(result["refresh_token"], str)
            )
            or not math.isfinite(expires_in)
            or expires_in <= 0
        ):
            raise BrivoError("Brivo returned an invalid token response") from None
        self.tokens.update(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token") or self.tokens.get("refresh_token"),
            expires_at=time.time() + expires_in,
        )
        self._dirty = True
        await self._save_tokens()

    async def redeem_invitation(self, email: str, code: str) -> None:
        """Explicit one-shot redemption. Caller must guard against replay."""
        async with self._operation():
            try:
                result = await self._request(
                    "POST",
                    AUTH_URL,
                    headers={"Authorization": self._basic()},
                    data={"grant_type": "invitation", "email": email, "access_code": code},
                )
            except HttpFailure as exc:
                if exc.status in (400, 401, 403, 410):
                    raise BrivoAuthError("Invitation invalid, expired, or already used") from None
                raise
            await self._accept_tokens(result)

    async def _refresh(self) -> None:
        refresh = self.tokens.get("refresh_token")
        if not refresh:
            raise BrivoAuthError("No refresh token; reauthenticate Brivo Mobile Pass")
        try:
            result = await self._request(
                "POST",
                AUTH_URL,
                headers={"Authorization": self._basic()},
                params={"grant_type": "refresh_token", "refresh_token": refresh},
                data=b"",
            )
        except HttpFailure as exc:
            if exc.status in (400, 401, 403, 410):
                raise BrivoAuthError("Brivo credentials expired or were revoked") from None
            raise
        await self._accept_tokens(result)

    async def _api(self, method: str, path: str, body: Any = None) -> Any:
        await self._save_tokens()
        if (
            not self.tokens.get("access_token")
            or self.tokens.get("expires_at", 0) < time.time() + 60
        ):
            await self._refresh()
        for attempt in range(2):
            try:
                return await self._request(
                    method,
                    API_URL + "/" + path,
                    headers={"Authorization": "Bearer " + self.tokens["access_token"]},
                    **({"json": body} if body is not None else {}),
                )
            except HttpFailure as exc:
                if exc.status == 401 and attempt == 0:
                    await self._refresh()
                elif exc.status == 401:
                    raise BrivoAuthError("Brivo rejected this credential") from None
                else:
                    raise
        return None

    async def check_connection(self) -> None:
        """Check authentication without requesting an unlock."""
        async with self._operation():
            if await self._read_credential() != self.credential:
                raise BrivoAuthError("Saved credential does not match this Mobile Pass") from None

    async def open_access_point(self, access_point_id: str) -> None:
        """Open only a discovered access point. Never retry uncertain failures."""
        async with self._operation():
            if not any(str(p["id"]) == access_point_id for p in self.access_points):
                raise BrivoError(
                    "This access point has not been configured", category="access_point_unavailable"
                ) from None
            await self._api(
                "POST",
                "access-points/" + quote(access_point_id, safe="") + "/unlock",
                {"credentialValue": self.credential, "proximity": {"wifi": []}},
            )

    async def _pages(self, path: str) -> list[dict[str, Any]]:
        offset = 0
        points = []
        while True:
            response = await self._api("GET", path + f"?offset={offset}&filter=")
            if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                raise BrivoError("Brivo returned an invalid list") from None
            batch = response["data"]
            for item in batch:
                if (
                    not isinstance(item, dict)
                    or type(item.get("id")) not in (str, int)
                    or item["id"] == ""
                    or (item.get("name") is not None and not isinstance(item["name"], str))
                    or (
                        "activationEnabled" in item
                        and not isinstance(item["activationEnabled"], bool)
                    )
                ):
                    raise BrivoError("Brivo returned an invalid list") from None
            offset += len(batch)
            count = response.get("count", offset)
            if isinstance(count, str) and count.isdecimal():
                try:
                    count = int(count)
                except ValueError:
                    raise BrivoError("Brivo returned an invalid list") from None
            if type(count) is not int or count < offset or (not batch and count > offset):
                raise BrivoError("Brivo returned an invalid list") from None
            points.extend(batch)
            if offset >= count:
                return points

    async def _read_credential(self) -> str:
        result = await self._api("GET", "pass")
        if not isinstance(result, dict):
            raise BrivoError("Brivo returned an invalid Mobile Pass response") from None
        credential = result.get("pass")
        if credential is None or credential == "":
            raise BrivoAuthError("Brivo returned no Mobile Pass credential") from None
        if not isinstance(credential, str):
            raise BrivoError("Brivo returned an invalid Mobile Pass response") from None
        return credential

    async def _discovery_snapshot(
        self, *, same_credential: bool = False
    ) -> tuple[str, list[dict[str, Any]]]:
        credential = await self._read_credential()
        if same_credential and credential != self.credential:
            raise BrivoAuthError("Saved credential does not match this Mobile Pass") from None
        points = []
        for site in await self._pages("sites"):
            for point in await self._pages(
                "sites/" + quote(str(site["id"]), safe="") + "/access-points"
            ):
                if point.get("activationEnabled") is False:
                    continue
                points.append(
                    {
                        "id": str(point["id"]),
                        "name": point.get("name") or "Access point",
                        "site_id": str(site["id"]),
                        "site_name": site.get("name") or "Brivo site",
                    }
                )
        return credential, points

    async def discover(self) -> None:
        """Read and atomically commit setup discovery; require at least one point."""
        async with self._operation():
            credential, points = await self._discovery_snapshot()
            if not points:
                raise BrivoError(
                    "No authorized, enabled access points found", category="no_access_points"
                ) from None
            self.credential, self.access_points = credential, points

    async def refresh_access_points(self) -> None:
        """Atomically refresh authorized points; an empty snapshot is valid."""
        async with self._operation():
            credential, points = await self._discovery_snapshot(same_credential=True)
            self.credential, self.access_points = credential, points
