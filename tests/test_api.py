"""Protocol and persistence behavior using fake HTTP responses."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.brivo_mobile_pass.api import (
    API_URL,
    AUTH_URL,
    BrivoAuthError,
    BrivoClient,
    BrivoError,
)


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


async def test_diagnostics_start_empty_and_record_successful_provider_request():
    api, session = client([Response({"pass": "test-credential"})])
    assert api.diagnostics == {
        "last_successful_request_at": None,
        "last_error": None,
        "last_error_at": None,
    }
    before = time.time()
    await api.check_connection()
    assert before <= api.diagnostics["last_successful_request_at"] <= time.time()
    assert api.diagnostics["last_error"] is None
    assert session.request.call_count == 1
    snapshot = api.diagnostics
    snapshot["last_error"] = "injected-canary"
    assert api.diagnostics["last_error"] is None


async def test_connection_failure_diagnostics_clear_only_after_completed_operation(caplog):
    caplog.set_level("DEBUG", logger="custom_components.brivo_mobile_pass.api")
    api, session = client(
        [aiohttp.ClientConnectionError("https://secret-canary.invalid/token-canary"), Response()]
    )
    before = time.time()
    with pytest.raises(BrivoError) as error:
        await api.open_access_point("101")
    assert api.diagnostics["last_error"] == "connection"
    assert before <= api.diagnostics["last_error_at"] <= time.time()
    assert api.diagnostics["last_successful_request_at"] is None
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert "canary" not in str(error.value)
    assert "canary" not in caplog.text
    assert session.request.call_count == 1
    await api.open_access_point("101")
    assert api.diagnostics["last_error"] is None
    assert api.diagnostics["last_error_at"] is None
    assert api.diagnostics["last_successful_request_at"] is not None


@pytest.mark.parametrize(
    ("responses", "persist_error", "category", "request_succeeded"),
    [
        ([TimeoutError("timeout-canary")], None, "timeout", False),
        ([Response(status=503)], None, "http", False),
        (
            [
                Response(status=401),
                Response({"access_token": "token-canary"}),
                Response(status=401),
            ],
            None,
            "auth",
            True,
        ),
        (
            [Response(status=401), Response({"access_token": "token-canary"})],
            OSError("disk-secret-canary"),
            "credentials_persistence",
            True,
        ),
    ],
)
async def test_diagnostics_categorize_final_failure_without_sensitive_details(
    responses, persist_error, category, request_succeeded, caplog
):
    caplog.set_level("DEBUG", logger="custom_components.brivo_mobile_pass.api")
    api, _ = client(responses, AsyncMock(side_effect=persist_error))
    with pytest.raises(BrivoError) as error:
        await api.open_access_point("101")
    assert api.diagnostics["last_error"] == category
    assert (api.diagnostics["last_successful_request_at"] is not None) == request_succeeded
    assert "canary" not in json.dumps(api.diagnostics)
    assert "canary" not in str(error.value)
    assert "canary" not in caplog.text
    if category in ("timeout", "auth", "credentials_persistence"):
        assert error.value.__cause__ is None
        assert error.value.__suppress_context__


@pytest.mark.parametrize(
    ("operation", "args", "success_responses"),
    [
        (
            "redeem_invitation",
            ("email-canary", "invitation-canary"),
            [Response({"access_token": "new"})],
        ),
        ("check_connection", (), [Response({"pass": "test-credential"})]),
        (
            "discover",
            (),
            [
                Response({"pass": "test-credential"}),
                Response({"data": [{"id": 1}], "count": 1}),
                Response({"data": [{"id": 303}], "count": 1}),
            ],
        ),
        (
            "refresh_access_points",
            (),
            [Response({"pass": "test-credential"}), Response({"data": [], "count": 0})],
        ),
    ],
)
async def test_each_high_level_operation_records_failure_then_clears_on_success(
    operation, args, success_responses
):
    api, _ = client([aiohttp.ClientConnectionError("private-canary"), *success_responses])
    with pytest.raises(BrivoError):
        await getattr(api, operation)(*args)
    assert api.diagnostics["last_error"] == "connection"
    await getattr(api, operation)(*args)
    assert api.diagnostics["last_error"] is None
    assert api.diagnostics["last_error_at"] is None


@pytest.mark.parametrize(
    ("operation", "args", "responses", "category"),
    [
        (
            "discover",
            (),
            [Response({"pass": "replacement-canary"}), Response({"data": [], "count": 0})],
            "no_access_points",
        ),
        ("open_access_point", ("unknown-id-canary",), [], "access_point_unavailable"),
    ],
)
async def test_local_operation_rejections_have_specific_categories(
    operation, args, responses, category
):
    api, _ = client(responses)
    original_points = list(api.access_points)
    with pytest.raises(BrivoError):
        await getattr(api, operation)(*args)
    assert api.diagnostics["last_error"] == category
    assert api.credential == "test-credential"
    assert api.access_points == original_points


@pytest.mark.parametrize(
    "token_result",
    [
        {"access_token": ["access-canary"]},
        {"access_token": "new-access", "refresh_token": {"secret": "refresh-canary"}},
        {"access_token": "new-access", "expires_in": "expiry-canary"},
        {"access_token": "new-access", "expires_in": None},
        {"access_token": "new-access", "expires_in": "NaN"},
        {"access_token": "new-access", "expires_in": -1},
    ],
)
async def test_invalid_token_response_is_sanitized_without_corrupting_rotation(
    token_result, caplog
):
    caplog.set_level("DEBUG", logger="custom_components.brivo_mobile_pass.api")
    api, session = client([Response(token_result), Response()])
    api.tokens["expires_at"] = 0
    original_tokens = dict(api.tokens)
    with pytest.raises(BrivoError, match="invalid") as error:
        await api.open_access_point("101")
    assert api.diagnostics["last_error"] == "invalid_response"
    assert api.tokens == original_tokens
    api.persist.assert_not_awaited()
    assert session.request.call_count == 1
    assert "canary" not in str(error.value)
    assert "canary" not in caplog.text
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("operation", ["check_connection", "discover", "refresh_access_points"])
@pytest.mark.parametrize("credential", [{"private": "pass-canary"}, ["pass-canary"], 123, True])
async def test_malformed_pass_cannot_become_identity(operation, credential):
    api, _ = client(
        [
            Response({"pass": credential}),
            Response({"data": [{"id": 1}], "count": 1}),
            Response({"data": [{"id": 303}], "count": 1}),
        ]
    )
    original_points = list(api.access_points)
    with pytest.raises(BrivoError, match="invalid"):
        await getattr(api, operation)()
    assert api.diagnostics["last_error"] == "invalid_response"
    assert api.credential == "test-credential"
    assert api.access_points == original_points


@pytest.mark.parametrize("raw", [b"response-body-canary", b"\xff"])
async def test_invalid_response_body_records_category_without_success_or_debug_leaks(raw, caplog):
    caplog.set_level("DEBUG", logger="custom_components.brivo_mobile_pass.api")
    response = Response()
    response.read = AsyncMock(return_value=raw)
    api, session = client([response])
    with pytest.raises(BrivoError, match="invalid response") as error:
        await api.check_connection()
    assert api.diagnostics["last_error"] == "invalid_response"
    assert api.diagnostics["last_successful_request_at"] is None
    assert "canary" not in json.dumps(api.diagnostics)
    assert "canary" not in str(error.value)
    assert "canary" not in caplog.text
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert session.request.call_count == 1


async def test_intermediate_refresh_success_does_not_clear_previous_error():
    api, session = client(
        [
            aiohttp.ClientConnectionError("private-canary"),
            Response({"access_token": "rotated-access", "refresh_token": "rotated-refresh"}),
            Response(status=503),
        ]
    )
    with pytest.raises(BrivoError):
        await api.check_connection()
    prior_error_at = api.diagnostics["last_error_at"]

    async def persist(tokens):
        assert tokens["refresh_token"] == "rotated-refresh"
        assert api.diagnostics["last_error"] == "connection"
        assert api.diagnostics["last_error_at"] == prior_error_at
        assert api.diagnostics["last_successful_request_at"] is not None

    api.persist = persist
    api.tokens["expires_at"] = 0
    with pytest.raises(BrivoError):
        await api.open_access_point("101")
    assert api.diagnostics["last_error"] == "http"
    assert session.request.call_count == 3


async def test_cancelled_refresh_keeps_snapshot_and_previous_operation_error():
    entered = asyncio.Event()
    blocked_response = Response({"data": [], "count": 0})

    async def read():
        entered.set()
        await asyncio.Event().wait()

    blocked_response.read = read
    api, _ = client(
        [
            aiohttp.ClientConnectionError("private-canary"),
            Response({"pass": "test-credential"}),
            blocked_response,
        ]
    )
    with pytest.raises(BrivoError):
        await api.check_connection()
    previous_error_at = api.diagnostics["last_error_at"]
    original_points = list(api.access_points)
    refresh = asyncio.create_task(api.refresh_access_points())
    await entered.wait()
    refresh.cancel()
    with pytest.raises(asyncio.CancelledError):
        await refresh
    assert not api.lock.locked()
    assert api.credential == "test-credential"
    assert api.access_points == original_points
    assert api.diagnostics["last_error"] == "connection"
    assert api.diagnostics["last_error_at"] == previous_error_at


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


@pytest.mark.parametrize("points", [[], [{"id": 303, "name": "New gate"}]])
async def test_refresh_replaces_snapshot_without_unlock_or_redemption(points):
    api, session = client(
        [
            Response({"pass": "test-credential"}),
            Response({"data": [{"id": 7, "name": "New site"}], "count": 1}),
            Response({"data": points, "count": len(points)}),
        ]
    )
    await api.refresh_access_points()
    assert api.credential == "test-credential"
    assert api.access_points == [
        {"id": str(point["id"]), "name": point["name"], "site_id": "7", "site_name": "New site"}
        for point in points
    ]
    assert [call.args[0] for call in session.request.call_args_list] == ["GET"] * 3


async def test_refresh_rejects_changed_pass_before_reading_sites():
    api, session = client(
        [Response({"pass": "other-pass-canary"}), Response({"data": [], "count": 0})]
    )
    original_points = list(api.access_points)
    with pytest.raises(BrivoAuthError, match="does not match") as error:
        await api.refresh_access_points()
    assert "other-pass-canary" not in str(error.value)
    assert api.credential == "test-credential"
    assert api.access_points == original_points
    assert session.request.call_count == 1


@pytest.mark.parametrize("expired", [False, True])
async def test_refresh_can_rotate_tokens_without_unlocking_or_redeeming(expired):
    persist = AsyncMock()
    api, session = client(
        [
            *([] if expired else [Response(status=401)]),
            Response({"access_token": "new-access", "refresh_token": "new-refresh"}),
            Response({"pass": "test-credential"}),
            Response({"data": [], "count": 0}),
        ],
        persist,
    )
    if expired:
        api.tokens["expires_at"] = 0
    await api.refresh_access_points()
    assert api.access_points == []
    assert persist.await_args.args[0]["refresh_token"] == "new-refresh"
    for call in session.request.call_args_list:
        if call.args[0] == "POST":
            assert call.args[1] == AUTH_URL
            assert call.kwargs["params"]["grant_type"] == "refresh_token"
        else:
            assert call.args[0] == "GET"
            assert "/unlock" not in call.args[1]
    assert api.diagnostics["last_error"] is None


async def test_later_site_refresh_failure_keeps_original_snapshot():
    api, _ = client(
        [
            Response({"pass": "test-credential"}),
            Response({"data": [{"id": 1}, {"id": 2}], "count": 2}),
            Response({"data": [{"id": 303}], "count": 1}),
            Response(status=503),
        ]
    )
    original_points = list(api.access_points)
    with pytest.raises(BrivoError):
        await api.refresh_access_points()
    assert api.credential == "test-credential"
    assert api.access_points == original_points
    assert api.diagnostics["last_error"] == "http"


@pytest.mark.parametrize("operation", ["discover", "refresh_access_points"])
@pytest.mark.parametrize(
    "pages",
    [
        [{"data": ["site-body-canary"], "count": 1}],
        [{"data": [{"name": "site-name-canary"}], "count": 1}],
        [{"data": [{"id": {"private": "site-id-canary"}}], "count": 1}],
        [{"data": [{"id": 1}], "count": "count-canary"}],
        [{"data": [{"id": 1}], "count": -1}],
        [{"data": [], "count": 1}],
        [{"data": [{"id": 1}], "count": 1}, {"data": ["point-body-canary"], "count": 1}],
        [
            {"data": [{"id": 1}], "count": 1},
            {"data": [{"id": {"private": "point-id-canary"}}], "count": 1},
        ],
        [
            {"data": [{"id": 1}], "count": 1},
            {"data": [{"id": 3, "name": {"private": "point-name-canary"}}], "count": 1},
        ],
        [{"data": [], "count": "9" * 5000}],
        [
            {"data": [{"id": 1}], "count": 1},
            {"data": [{"name": "missing-id-canary"}], "count": 1},
        ],
        [
            {"data": [{"id": 1}], "count": 1},
            {"data": [{"id": 3, "activationEnabled": "false"}], "count": 1},
        ],
    ],
)
async def test_malformed_discovery_is_sanitized_and_keeps_snapshot(operation, pages):
    api, _ = client([Response({"pass": "test-credential"}), *[Response(p) for p in pages]])
    original_points = list(api.access_points)
    with pytest.raises(BrivoError, match="invalid") as error:
        await getattr(api, operation)()
    assert "canary" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    assert api.credential == "test-credential"
    assert api.access_points == original_points


async def test_failed_setup_discovery_preserves_complete_snapshot():
    api, _ = client(
        [
            Response({"pass": "replacement-credential"}),
            Response({"data": [{"id": 1}], "count": 1}),
            Response(status=503),
        ]
    )
    original_points = list(api.access_points)
    with pytest.raises(BrivoError):
        await api.discover()
    assert api.credential == "test-credential"
    assert api.access_points == original_points


async def test_invitation_is_one_request_and_persists_result():
    persist = AsyncMock()
    api, session = client(
        [Response({"access_token": "new", "refresh_token": "new-refresh"})], persist
    )
    await api.redeem_invitation("example@example.invalid", "test-invitation")
    assert session.request.call_count == 1
    assert session.request.call_args.kwargs["data"]["grant_type"] == "invitation"
    persist.assert_awaited_once()


async def test_waiting_unlock_rechecks_membership_after_refresh_removes_point():
    api, session = client(
        [Response({"pass": "test-credential"}), Response({"data": [], "count": 0}), Response()]
    )
    await api.lock.acquire()
    refresh = asyncio.create_task(api.refresh_access_points())
    await asyncio.sleep(0)
    unlock = asyncio.create_task(api.open_access_point("101"))
    await asyncio.sleep(0)
    assert session.request.call_count == 0
    api.lock.release()
    await refresh
    with pytest.raises(BrivoError, match="not been configured"):
        await unlock
    assert api.access_points == []
    assert session.request.call_count == 2


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
