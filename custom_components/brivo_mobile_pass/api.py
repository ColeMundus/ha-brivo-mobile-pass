"""Brivo Mobile Pass HTTP client, independent of Home Assistant."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import aiohttp

AUTH_URL = "https://auth.brivo.com/oauth/token"
API_URL = "https://pi.brivo.com/api"


class BrivoError(Exception):
    """An error safe to show without exposing credentials."""


class BrivoAuthError(BrivoError):
    """Credentials require reauthentication."""


class HttpFailure(BrivoError):
    """An HTTP error with no sensitive response or URL."""

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

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            async with self.session.request(
                method, url, timeout=aiohttp.ClientTimeout(total=25), **kwargs
            ) as response:
                if response.status >= 400:
                    raise HttpFailure(response.status)
                raw = await response.read()
                return json.loads(raw) if raw else None
        except TimeoutError, aiohttp.ClientError:
            raise BrivoError("Brivo connection failed; command was not retried") from None
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
                raise BrivoError("Could not save credentials; unlock was not sent") from None
            self._dirty = False

    async def _accept_tokens(self, result: Any) -> None:
        if not isinstance(result, dict) or not result.get("access_token"):
            raise BrivoError("Brivo returned no access token")
        self.tokens.update(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token") or self.tokens.get("refresh_token"),
            expires_at=time.time() + float(result.get("expires_in", 300)),
        )
        self._dirty = True
        await self._save_tokens()

    async def redeem_invitation(self, email: str, code: str) -> None:
        """Explicit one-shot redemption. Caller must guard against replay."""
        async with self.lock:
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
        async with self.lock:
            result = await self._api("GET", "pass")
            if not isinstance(result, dict) or result.get("pass") != self.credential:
                raise BrivoAuthError("Saved credential does not match this Mobile Pass")

    async def open_access_point(self, access_point_id: str) -> None:
        """Open only a discovered access point. Never retry uncertain failures."""
        if not any(str(p["id"]) == access_point_id for p in self.access_points):
            raise BrivoError("This access point has not been configured")
        async with self.lock:
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
                raise BrivoError("Brivo returned an invalid list")
            batch = response["data"]
            points.extend(batch)
            offset += len(batch)
            if not batch or offset >= int(response.get("count", offset)):
                return points

    async def discover(self) -> None:
        """Read credential, sites, and access points without unlocking anything."""
        async with self.lock:
            result = await self._api("GET", "pass")
            if not isinstance(result, dict) or not result.get("pass"):
                raise BrivoAuthError("Brivo returned no Mobile Pass credential")
            self.credential = result["pass"]
            points = []
            for site in await self._pages("sites"):
                for point in await self._pages(
                    "sites/" + quote(str(site["id"]), safe="") + "/access-points"
                ):
                    if point.get("id") is None or point.get("activationEnabled") is False:
                        continue
                    points.append(
                        {
                            "id": str(point["id"]),
                            "name": point.get("name") or "Access point",
                            "site_id": str(site["id"]),
                            "site_name": site.get("name") or "Brivo site",
                        }
                    )
            if not points:
                raise BrivoError("No authorized, enabled access points found")
            self.access_points = points
