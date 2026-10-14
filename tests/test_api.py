"""Protocol and persistence behavior using fake HTTP responses."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.brivo_mobile_pass.api import API_URL, AUTH_URL, BrivoClient, BrivoError


class Response:
    def __init__(self, body=None, status=200):
        self.body, self.status = body, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return json.dumps(self.body).encode() if self.body is not None else b""


def client(responses, persist=None):
    session = MagicMock()
    session.request.side_effect = responses
    instance = BrivoClient(
        session,
        {"client_id": "test-client", "client_secret": "test-secret"},
        "test-credential",
        [{"id": "101", "name": "Gate A"}, {"id": "202", "name": "Gate B"}],
        {
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "expires_at": time.time() + 3600,
        },
        persist or AsyncMock(),
    )
    return instance, session


async def test_each_button_uses_its_own_target():
    api, session = client([Response(), Response()])
    await api.open_access_point("202")
    await api.open_access_point("101")
    assert [call.args for call in session.request.call_args_list] == [
        ("POST", API_URL + "/access-points/202/unlock"),
        ("POST", API_URL + "/access-points/101/unlock"),
    ]
    assert session.request.call_args.kwargs["json"] == {
        "credentialValue": "test-credential",
        "proximity": {"wifi": []},
    }
    with pytest.raises(BrivoError):
        await api.open_access_point("303")
    assert session.request.call_count == 2


async def test_rotation_is_saved_before_retry():
    persisted = []

    async def save(tokens):
        persisted.append(tokens)

    api, session = client(
        [
            Response(status=401),
            Response(
                {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 300}
            ),
            Response(),
        ],
        save,
    )
    await api.open_access_point("101")
    assert persisted[0]["refresh_token"] == "new-refresh"
    assert session.request.call_args.kwargs["headers"]["Authorization"] == "Bearer new-access"
    assert session.request.call_args_list[1].args == ("POST", AUTH_URL)
    assert session.request.call_args_list[1].kwargs["params"]["refresh_token"] == "test-refresh"


async def test_failed_persistence_prevents_unlock_and_retries_save():
    persist = AsyncMock(side_effect=[OSError("disk full"), None])
    api, session = client(
        [Response({"access_token": "new", "refresh_token": "rotated"}), Response()], persist
    )
    api.tokens["expires_at"] = 0
    with pytest.raises(BrivoError, match="Could not save"):
        await api.open_access_point("101")
    assert session.request.call_count == 1
    await api.open_access_point("101")
    assert session.request.call_count == 2
    assert persist.await_count == 2


async def test_uncertain_unlock_is_never_retried():
    api, session = client([aiohttp.ClientConnectionError("sensitive URL")])
    with pytest.raises(BrivoError, match="not retried") as error:
        await api.open_access_point("101")
    assert "sensitive" not in str(error.value)
    assert session.request.call_count == 1


async def test_discovery_paginates_and_never_opens():
    api, session = client(
        [
            Response({"pass": "test-credential"}),
            Response({"data": [{"id": 1, "name": "Example site"}], "count": 1}),
            Response(
                {"data": [{"id": 101, "name": "Gate A", "activationEnabled": True}], "count": 3}
            ),
            Response(
                {
                    "data": [
                        {"id": 202, "name": "Gate B"},
                        {"id": 303, "activationEnabled": False},
                    ],
                    "count": 3,
                }
            ),
        ]
    )
    await api.discover()
    assert [p["id"] for p in api.access_points] == ["101", "202"]
    assert all(call.args[0] == "GET" for call in session.request.call_args_list)
    assert "offset=1" in session.request.call_args.args[1]


async def test_invitation_is_one_request_and_persists_result():
    persist = AsyncMock()
    api, session = client(
        [Response({"access_token": "new", "refresh_token": "new-refresh"})], persist
    )
    await api.redeem_invitation("example@example.invalid", "test-invitation")
    assert session.request.call_count == 1
    assert session.request.call_args.kwargs["data"]["grant_type"] == "invitation"
    persist.assert_awaited_once()


async def test_concurrent_commands_serialize():
    api, _ = client([])
    active = 0

    async def request(*args, **kwargs):
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(0)
        active -= 1

    api._api = request
    await asyncio.gather(api.open_access_point("101"), api.open_access_point("202"))
