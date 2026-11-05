"""Read-only discovery and dynamic entities in the real HA harness."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_api import Response
from test_integration import DATA, setup

from custom_components.brivo_mobile_pass.api import BrivoAuthError, BrivoError
from custom_components.brivo_mobile_pass.const import DOMAIN, SIGNAL_ACCESS_POINTS_UPDATED

POINT = {"id": "202", "name": "New door", "site_id": "10", "site_name": "Example site"}


def test_service_description_and_translation_parity():
    root = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    strings = json.loads((root / "strings.json").read_text())
    english = json.loads((root / "translations/en.json").read_text())
    services = yaml.safe_load((root / "services.yaml").read_text())
    assert strings == english
    assert "refresh_access_points" in services
    assert services["refresh_access_points"] == services["check_connection"]
    translated = strings["services"]["refresh_access_points"]
    assert translated["name"] == "Refresh access points"
    assert "without opening" in translated["description"]
    assert translated["fields"] == strings["services"]["check_connection"]["fields"]


async def refresh(hass, entry, points):
    async def discover():
        entry.runtime_data.client.access_points = deepcopy(points)

    with patch.object(
        entry.runtime_data.client,
        "refresh_access_points",
        new=AsyncMock(side_effect=discover),
        create=True,
    ) as request:
        await hass.services.async_call(
            DOMAIN, "refresh_access_points", {"config_entry_id": entry.entry_id}, blocking=True
        )
        await hass.async_block_till_done()
    request.assert_awaited_once()


async def test_refresh_persists_snapshot_without_reloading_or_changing_identity(hass):
    entry = await setup(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "unrelated": "keep", "tokens": {"refresh_token": "pending-reauth"}},
    )
    client = entry.runtime_data.client
    saved = deepcopy(dict(entry.data))
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        await refresh(hass, entry, [POINT])
    assert entry.runtime_data.client is client
    assert dict(entry.data) == {**saved, "access_points": [POINT]}
    assert entry.data["application"] == DATA["application"]
    reload.assert_not_awaited()


async def test_reconcile_add_remove_reappear_rename_and_user_identity(hass):
    entry = await setup(hass)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    original_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_101")
    original = registry.async_get(original_id)
    registry.async_update_entity(original_id, name="My gate button")
    devices.async_update_device(original.device_id, name_by_user="My custom gate")
    renamed = {**DATA["access_points"][0], "name": "Renamed gate"}
    await refresh(hass, entry, [renamed, POINT])
    assert len(hass.states.async_all("button")) == 2
    assert registry.async_get(original_id).id == original.id
    assert registry.async_get(original_id).name == "My gate button"
    device = devices.async_get(original.device_id)
    assert device.name == "Renamed gate"
    assert device.name_by_user == "My custom gate"
    await refresh(hass, entry, [POINT])
    assert hass.states.get(original_id).state == STATE_UNAVAILABLE
    await refresh(hass, entry, [renamed, POINT])
    assert hass.states.get(original_id).state != STATE_UNAVAILABLE
    assert registry.async_get(original_id).id == original.id
    await refresh(hass, entry, [])
    assert all(state.state == STATE_UNAVAILABLE for state in hass.states.async_all("button"))


@pytest.mark.parametrize("points", [[POINT], []])
async def test_removed_identity_survives_reload_and_listener_cleanup(hass, points):
    entry = await setup(hass)
    registry = er.async_get(hass)
    old_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_101")
    old = registry.async_get(old_id)
    registry.async_update_entity(old_id, name="My retained button")
    dr.async_get(hass).async_update_device(old.device_id, name_by_user="My retained device")
    signal = f"{SIGNAL_ACCESS_POINTS_UPDATED}_{entry.entry_id}"
    assert len(hass.data["dispatcher"][signal]) == 1
    await refresh(hass, entry, points)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert signal not in hass.data["dispatcher"]
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert len(hass.data["dispatcher"][signal]) == 1
    assert hass.states.get(old_id).state == STATE_UNAVAILABLE
    await refresh(hass, entry, [{**DATA["access_points"][0], "name": "Renamed on return"}, POINT])
    assert hass.states.get(old_id).state != STATE_UNAVAILABLE
    assert registry.async_get(old_id).id == old.id
    assert registry.async_get(old_id).name == "My retained button"
    device = dr.async_get(hass).async_get(old.device_id)
    assert device.name == "Renamed on return"
    assert device.name_by_user == "My retained device"
    assert len(hass.states.async_all("button")) == 2
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert signal not in hass.data["dispatcher"]


async def test_disabled_entity_is_not_reenabled_or_duplicated(hass):
    entry = await setup(hass)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_101")
    original = registry.async_get(entity_id)
    registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    await refresh(hass, entry, [])
    await refresh(hass, entry, DATA["access_points"])
    assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(entity_id).id == original.id
    assert hass.states.get(entity_id) is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await refresh(hass, entry, [DATA["access_points"][0], POINT])
    assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert hass.states.get(entity_id) is None
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 2


@pytest.mark.parametrize("error", [None, BrivoAuthError("old-session")])
async def test_inflight_refresh_cannot_update_reloaded_entry(hass, error):
    entry = await setup(hass)
    started, release = asyncio.Event(), asyncio.Event()
    old_client = entry.runtime_data.client

    async def discover():
        async with old_client._operation():
            started.set()
            await release.wait()
            if error:
                raise error
            old_client.access_points = [POINT]

    with (
        patch.object(old_client, "refresh_access_points", new=discover, create=True),
        patch.object(type(entry), "async_start_reauth") as reauth,
    ):
        task = asyncio.create_task(
            hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        )
        await started.wait()
        with patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
        ):
            reload_task = asyncio.create_task(hass.config_entries.async_reload(entry.entry_id))
            await asyncio.wait({reload_task}, timeout=0.05)
            assert not reload_task.done()
            release.set()
            assert await reload_task
        with pytest.raises(HomeAssistantError):
            await task
        reauth.assert_not_called()
    assert entry.runtime_data.client is not old_client
    assert entry.data["access_points"] == DATA["access_points"]
    assert len(hass.states.async_all("button")) == 1


async def test_concurrent_real_refreshes_publish_atomic_read_only_snapshots(hass):
    entry = await setup(hass)
    client = entry.runtime_data.client
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    generation = 0

    async def provider(method, url, **kwargs):
        nonlocal generation
        calls.append((method, url))
        assert method == "GET"
        if url.endswith("/pass"):
            generation += 1
            if generation == 1:
                started.set()
                await release.wait()
            return {"pass": DATA["credential"]}
        if "/sites?" in url:
            return {"data": [{"id": "10", "name": "Example site"}], "count": 1}
        assert "/access-points?" in url
        return {
            "data": [{"id": "202", "name": "New door"}] if generation == 1 else [],
            "count": 1 if generation == 1 else 0,
        }

    with patch.object(client, "_request", new=provider):
        first = asyncio.create_task(
            hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        )
        await started.wait()
        second = asyncio.create_task(
            hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        )
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert entry.data["access_points"] == DATA["access_points"]
        release.set()
        await asyncio.gather(first, second)
        await hass.async_block_till_done()
    assert generation == 2
    assert len(calls) == 6
    assert client.access_points == entry.data["access_points"] == []
    assert len(hass.states.async_all("button")) == 2
    assert all(state.state == STATE_UNAVAILABLE for state in hass.states.async_all("button"))
    assert len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 2


async def test_real_refresh_persists_rotated_tokens_with_existing_store_callback(hass):
    entry = await setup(hass)
    client = entry.runtime_data.client
    client.tokens["expires_at"] = 0
    store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens")
    previous = await store.async_load()

    async def provider(method, url, **kwargs):
        if method == "POST":
            assert url.endswith("/oauth/token")
            return {
                "access_token": "rotated",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            }
        assert method == "GET"
        if url.endswith("/pass"):
            return {"pass": DATA["credential"]}
        assert "/sites?" in url
        return {"data": [], "count": 0}

    with patch.object(client, "_request", new=provider):
        await hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        await hass.async_block_till_done()
    saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load()
    assert saved["bootstrap_id"] == previous["bootstrap_id"]
    assert saved["tokens"]["refresh_token"] == "rotated-refresh"
    assert client.tokens == saved["tokens"]
    assert "tokens" not in entry.data
    assert entry.data["access_points"] == []
    assert entry.data["application"] == DATA["application"]


@pytest.mark.parametrize("fail_first_save", [False, True])
@pytest.mark.parametrize("reauth", [False, True])
@pytest.mark.parametrize(
    "queued_operation",
    ["check_connection", "open_access_point", "discover", "refresh_access_points"],
)
async def test_reload_drains_real_rotation_before_successor_authenticates(
    hass, monkeypatch, reauth, queued_operation, fail_first_save
):
    """Reload must consume the last token, not race a retired Store writer."""
    started, release = asyncio.Event(), asyncio.Event()
    grants = []
    retired_pass_reads = 0

    class RotatingResponse(Response):
        async def read(self):
            started.set()
            await release.wait()
            return await super().read()

    def provider(method, url, **kwargs):
        nonlocal retired_pass_reads
        if method == "POST":
            assert url.endswith("/oauth/token")
            refresh_token = kwargs["params"]["refresh_token"]
            grants.append(refresh_token)
            if refresh_token == "test-refresh":
                return RotatingResponse(
                    {
                        "access_token": "retired",
                        "refresh_token": "retired-refresh",
                        "expires_in": 3600,
                    }
                )
            assert refresh_token == ("replacement-refresh" if reauth else "retired-refresh")
            return Response(
                {"access_token": "current", "refresh_token": "current-refresh", "expires_in": 3600}
            )
        assert method == "GET"
        if url.endswith("/pass"):
            if kwargs["headers"]["Authorization"] == "Bearer retired":
                retired_pass_reads += 1
                if fail_first_save or retired_pass_reads == 2:
                    return Response(status=401)
            return Response({"pass": DATA["credential"]})
        assert "/sites?" in url
        return Response({"data": [], "count": 0})

    session = MagicMock()
    session.request.side_effect = provider
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="test-unique")
    entry.add_to_hass(hass)
    with patch("custom_components.brivo_mobile_pass.async_get_clientsession", return_value=session):
        assert await hass.config_entries.async_setup(entry.entry_id)
        old_client = entry.runtime_data.client
        original_save = entry.runtime_data.store.async_save
        saves = 0

        async def save(tokens):
            nonlocal saves
            saves += 1
            if fail_first_save and saves == 1:
                raise OSError("private-canary")
            await original_save(tokens)

        monkeypatch.setattr(entry.runtime_data.store, "async_save", save)
        old_client.tokens["expires_at"] = 0
        refresh_task = asyncio.create_task(
            hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        )
        await started.wait()
        args = ("101",) if queued_operation == "open_access_point" else ()
        queued = asyncio.create_task(getattr(old_client, queued_operation)(*args))
        await asyncio.sleep(0)  # Queue behind the rotating operation's client lock.
        if reauth:
            # Same-pass reauth data is installed before HA initiates its reload.
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "tokens": {"refresh_token": "replacement-refresh", "expires_at": 0},
                },
            )
        reload_task = asyncio.create_task(hass.config_entries.async_reload(entry.entry_id))
        try:
            await asyncio.wait({reload_task}, timeout=0.05)
            successor_started_early = entry.runtime_data.client is not old_client
        finally:
            release.set()
        refresh_result, queued_result, reload_result = await asyncio.gather(
            refresh_task, queued, reload_task, return_exceptions=True
        )
        assert not successor_started_early
        assert reload_result is True
        assert isinstance(refresh_result, HomeAssistantError)
        assert isinstance(queued_result, BrivoError)
        assert str(queued_result) == "Brivo client is unloading; try again after reload"
        assert saves == (2 if fail_first_save else 1)
        assert not old_client._dirty
        assert grants == ["test-refresh", "replacement-refresh" if reauth else "retired-refresh"]
        active_client = entry.runtime_data.client
        assert active_client is not old_client
        saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load()
        assert saved["tokens"] == active_client.tokens
        assert saved["tokens"]["refresh_token"] == "current-refresh"
        previous_calls = session.request.call_count
        with pytest.raises(BrivoError, match="unloading"):
            await getattr(old_client, queued_operation)(*args)
        assert session.request.call_count == previous_calls
        assert entry.data["access_points"] == DATA["access_points"]


async def test_reload_persistence_failure_retains_rotated_runtime_without_successor(hass):
    entry = await setup(hass)
    runtime = entry.runtime_data
    client = runtime.client
    client.tokens["expires_at"] = 0
    started, release = asyncio.Event(), asyncio.Event()

    class RotatingResponse(Response):
        async def read(self):
            started.set()
            await release.wait()
            return await super().read()

    session = MagicMock()
    session.request.side_effect = [
        RotatingResponse(
            {"access_token": "rotated", "refresh_token": "rotated-refresh", "expires_in": 3600}
        ),
        Response({"pass": DATA["credential"]}),
    ]
    client.session = session
    previous = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load()
    with (
        patch.object(runtime.store, "async_save", side_effect=OSError("private-canary")) as save,
        patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock()) as platforms,
        patch("custom_components.brivo_mobile_pass.async_get_clientsession") as successor_session,
    ):
        task = asyncio.create_task(
            hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        )
        await started.wait()
        reload_task = asyncio.create_task(hass.config_entries.async_reload(entry.entry_id))
        await asyncio.wait({reload_task}, timeout=0.05)
        release.set()
        with pytest.raises(HomeAssistantError, match="Could not refresh"):
            await task
        assert await reload_task is False
        assert save.await_count == 2  # Operation save and final drain retry.
        platforms.assert_not_awaited()
        successor_session.assert_not_called()
    assert entry.state is ConfigEntryState.FAILED_UNLOAD
    assert entry.runtime_data is runtime
    assert client._dirty
    assert not client._closing
    assert client.tokens["refresh_token"] == "rotated-refresh"
    assert await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load() == previous
    assert session.request.call_count == 1
    # The retained client can retry persistence without refreshing/reusing the old
    # token. HA's FAILED_UNLOAD state itself requires a restart to reload again.
    await client.check_connection()
    assert not client._dirty
    saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load()
    assert saved["tokens"] == client.tokens
    assert saved["bootstrap_id"] == previous["bootstrap_id"]


@pytest.mark.parametrize("platform_result", [False, OSError("private-canary")])
async def test_failed_platform_unload_reopens_same_client(hass, platform_result):
    entry = await setup(hass)
    client = entry.runtime_data.client
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(
            return_value=platform_result if platform_result is False else None,
            side_effect=platform_result if isinstance(platform_result, Exception) else None,
        ),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id) is False
    assert entry.runtime_data.client is client
    assert not client._closing
    client.session = MagicMock()
    client.session.request.return_value = Response({"pass": DATA["credential"]})
    await client.check_connection()
    assert client.session.request.call_count == 1


async def test_cancelled_drain_retains_client_and_does_not_cancel_active_rotation(hass):
    from custom_components.brivo_mobile_pass import async_unload_entry

    entry = await setup(hass)
    client = entry.runtime_data.client
    started, release = asyncio.Event(), asyncio.Event()

    class HeldResponse(Response):
        async def read(self):
            started.set()
            await release.wait()
            return await super().read()

    client.tokens["expires_at"] = 0
    client.session = MagicMock()
    client.session.request.side_effect = [
        HeldResponse(
            {"access_token": "rotated", "refresh_token": "rotated-refresh", "expires_in": 3600}
        ),
        Response({"pass": DATA["credential"]}),
    ]
    operation = asyncio.create_task(client.check_connection())
    await started.wait()
    with patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock()) as platforms:
        unload = asyncio.create_task(async_unload_entry(hass, entry))
        await asyncio.sleep(0)
        assert client._closing
        unload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await unload
        assert not client._closing
        assert not operation.done()
        platforms.assert_not_awaited()
    release.set()
    await operation
    saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load()
    assert saved["tokens"] == client.tokens
    assert saved["tokens"]["refresh_token"] == "rotated-refresh"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_removed_button_service_never_calls_command_client(hass, caplog):
    entry = await setup(hass)
    entity_id = hass.states.async_all("button")[0].entity_id
    await refresh(hass, entry, [])
    with patch.object(entry.runtime_data.client, "open_access_point", new=AsyncMock()) as command:
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
        command.assert_not_awaited()
    assert "not currently available" in caplog.text


async def test_real_refresh_token_storage_failure_preserves_snapshot(hass):
    entry = await setup(hass)
    client = entry.runtime_data.client
    client.tokens["expires_at"] = 0
    previous = deepcopy(dict(entry.data))
    with (
        patch.object(
            client,
            "_request",
            new=AsyncMock(
                return_value={
                    "access_token": "rotated",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                }
            ),
        ) as provider,
        patch.object(
            entry.runtime_data.store,
            "async_save",
            new=AsyncMock(side_effect=OSError("private-canary")),
        ),
    ):
        with pytest.raises(HomeAssistantError, match="Could not refresh") as result:
            await hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        assert "private-canary" not in str(result.value)
        assert result.value.__suppress_context__
        assert provider.await_count == 1  # OAuth rotation only, never access-point requests.
    assert client.access_points == previous["access_points"]
    assert dict(entry.data) == previous
    assert client._dirty
    assert hass.states.async_all("button")[0].state != STATE_UNAVAILABLE


@pytest.mark.parametrize(
    "error",
    [BrivoAuthError("private-canary"), BrivoError("private-canary"), OSError("private-canary")],
)
async def test_refresh_failures_preserve_snapshot_and_hide_details(hass, error):
    entry = await setup(hass)
    previous = deepcopy(dict(entry.data))
    with (
        patch.object(
            entry.runtime_data.client,
            "refresh_access_points",
            new=AsyncMock(side_effect=error),
            create=True,
        ),
        patch.object(type(entry), "async_start_reauth") as reauth,
    ):
        with pytest.raises(HomeAssistantError) as result:
            await hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        assert "private-canary" not in str(result.value)
        assert result.value.__suppress_context__
        assert reauth.call_count == int(isinstance(error, BrivoAuthError))
    assert dict(entry.data) == previous
    assert entry.runtime_data.client.access_points == previous["access_points"]
    assert hass.states.async_all("button")[0].state != STATE_UNAVAILABLE


@pytest.mark.parametrize("service", ["check_connection", "refresh_access_points"])
async def test_services_select_only_one_loaded_pass(hass, service):
    entry = await setup(hass)
    client = entry.runtime_data.client
    with patch.object(client, service, new=AsyncMock(), create=True) as request:
        await hass.services.async_call(DOMAIN, service, {}, blocking=True)
        request.assert_awaited_once()
        request.reset_mock()
        other = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="second-pass")
        other.add_to_hass(hass)
        with patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
        ):
            assert await hass.config_entries.async_setup(other.entry_id)
            await hass.async_block_till_done()
        with pytest.raises(HomeAssistantError, match="Select one loaded"):
            await hass.services.async_call(DOMAIN, service, {}, blocking=True)
        with pytest.raises(HomeAssistantError, match="Select one loaded"):
            await hass.services.async_call(
                DOMAIN, service, {"config_entry_id": "unknown"}, blocking=True
            )
        await hass.services.async_call(
            DOMAIN, service, {"config_entry_id": entry.entry_id}, blocking=True
        )
        request.assert_awaited_once()
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED
        with pytest.raises(HomeAssistantError, match="Select one loaded"):
            await hass.services.async_call(
                DOMAIN, service, {"config_entry_id": entry.entry_id}, blocking=True
            )
        assert await hass.config_entries.async_unload(other.entry_id)
        with pytest.raises(HomeAssistantError, match="Select one loaded"):
            await hass.services.async_call(DOMAIN, service, {}, blocking=True)
