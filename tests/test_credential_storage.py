"""Credential persistence must be verified on disk, not in Store's cache."""

import asyncio
import json
import logging
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_api import Response
from test_config_flow import network, start
from test_integration import DATA, setup

from custom_components.brivo_mobile_pass.api import BrivoClient, BrivoError
from custom_components.brivo_mobile_pass.const import DOMAIN
from custom_components.brivo_mobile_pass.storage import _PrivateStorageLog, async_save_credentials


@pytest.fixture(autouse=True)
def isolate_private_storage_logs():
    """Restore process-lived registrations after HA and its executor have stopped."""
    loggers = [
        logging.getLogger(name)
        for name in ("homeassistant.helpers.storage", "homeassistant.util.file")
    ]
    filters = {logger: logger.filters.copy() for logger in loggers}
    identifiers = {
        redactor: set(redactor._identifiers)
        for logger in loggers
        for redactor in logger.filters
        if isinstance(redactor, _PrivateStorageLog)
    }
    yield
    for logger in loggers:
        for redactor in logger.filters:
            if isinstance(redactor, _PrivateStorageLog):
                redactor._identifiers = identifiers.get(redactor, set())
        logger.filters[:] = filters[logger]


@pytest.fixture
async def hass(isolate_private_storage_logs, hass):
    """Let the harness finish HA cleanup before restoring logging registrations."""
    yield hass


async def disk_data(hass, store):
    def read():
        return json.loads(Path(store.path).read_text())["data"]

    return await hass.async_add_executor_job(read)


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("error_type", [WriteError, SerializationError])
async def test_swallowed_disk_failure_during_real_rotation_reload(
    hass, persistent, error_type, caplog
):
    entry = await setup(hass)
    runtime = entry.runtime_data
    client = runtime.client
    previous = await disk_data(hass, runtime.store)
    client.tokens["expires_at"] = 0
    started, release = asyncio.Event(), asyncio.Event()

    class HeldRotation(Response):
        async def read(self):
            started.set()
            await release.wait()
            return await super().read()

    client.session = MagicMock()
    client.session.request.side_effect = [
        HeldRotation(
            {"access_token": "rotated", "refresh_token": "rotated-refresh", "expires_in": 3600}
        ),
        Response({"pass": DATA["credential"]}),
        Response({"data": [], "count": 0}),
    ]
    writes = 0
    original_write = runtime.store._async_write_data

    async def fail_below_save(data):
        nonlocal writes
        writes += 1
        if persistent or writes == 1:
            raise error_type("private-disk-canary")
        await original_write(data)

    with (
        patch.object(runtime.store, "_async_write_data", new=fail_below_save),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            wraps=hass.config_entries.async_unload_platforms,
        ) as platforms,
        patch("custom_components.brivo_mobile_pass.async_get_clientsession") as successor_session,
        patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
        ) as successor_check,
    ):
        # The old client's actual check/refresh and HA's actual Store exception
        # handler remain in use; only successor validation is isolated.
        operation = asyncio.create_task(
            hass.services.async_call(DOMAIN, "refresh_access_points", {}, blocking=True)
        )
        await started.wait()
        reload_task = asyncio.create_task(hass.config_entries.async_reload(entry.entry_id))
        await asyncio.wait({reload_task}, timeout=0.05)
        assert not reload_task.done()
        assert entry.runtime_data is runtime
        release.set()
        result = await asyncio.gather(operation, return_exceptions=True)
        reload_result = await reload_task
        assert isinstance(result[0], HomeAssistantError)
        assert writes == 2  # Failed rotation save followed by close's retry.
        assert reload_result is not persistent
        if persistent:
            assert client._dirty
            assert not client._closing
            assert entry.state is ConfigEntryState.FAILED_UNLOAD
            assert entry.runtime_data is runtime
            platforms.assert_not_awaited()
            successor_session.assert_not_called()
            successor_check.assert_not_awaited()
            assert hass.states.async_all("button")[0].state != STATE_UNAVAILABLE
            assert await disk_data(hass, runtime.store) == previous
        else:
            assert not client._dirty
            assert entry.runtime_data is not runtime
            assert entry.runtime_data.client.tokens == client.tokens
            assert (await disk_data(hass, runtime.store))["tokens"] == client.tokens
    assert "private-disk-canary" not in caplog.text
    if persistent:
        # Repair disk and retry without another OAuth grant or old-token reuse.
        await client.check_connection()
        assert not client._dirty
        assert (await disk_data(hass, runtime.store))["tokens"] == client.tokens
    assert client.tokens["refresh_token"] == "rotated-refresh"
    assert client.session.request.call_count == (2 if persistent else 1)


async def test_bootstrap_silent_failure_keeps_entry_and_staging_for_retry(hass):
    staging = Store(hass, 1, f"{DOMAIN}.setup.synthetic", private=True)
    await staging.async_save({"tokens": DATA["tokens"]})
    data = {**DATA, "staging_key": staging.key}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="bootstrap-test")
    entry.add_to_hass(hass)
    real_write = Store._async_write_data

    async def silent_write(store, envelope):
        if store.key.endswith(".tokens"):
            return
        await real_write(store, envelope)

    with (
        patch.object(Store, "_async_write_data", new=silent_write),
        patch.object(BrivoClient, "check_connection", new=AsyncMock()) as check,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        check.assert_not_awaited()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert dict(entry.data) == data
    assert (await disk_data(hass, staging))["tokens"] == DATA["tokens"]
    with patch.object(BrivoClient, "check_connection", new=AsyncMock()):
        assert await hass.config_entries.async_reload(entry.entry_id)
    assert "tokens" not in entry.data
    assert await hass.async_add_executor_job(Path(staging.path).exists) is False


@pytest.mark.parametrize("step", ["invitation", "token"])
async def test_flow_requires_durable_staging_before_provider_request(hass, step):
    result = await start(hass, step)
    user = (
        {"email": "example@example.invalid", "invitation_code": "synthetic-code"}
        if step == "invitation"
        else {"refresh_token": "synthetic-import"}
    )
    with (
        patch.object(Store, "_async_write_data", new=AsyncMock()),
        patch.object(BrivoClient, "_request", new=AsyncMock()) as request,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    request.assert_not_awaited()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("step", ["invitation", "manual_token"])
async def test_flow_retains_unsaved_rotation_until_disk_repair(hass, step, cancel, caplog):
    result = await start(hass, step)
    user = (
        {"email": "example@example.invalid", "invitation_code": "synthetic-code"}
        if step == "invitation"
        else {
            "client_id": "synthetic-client",
            "client_secret": "old-secret",
            "refresh_token": "synthetic-import",
        }
    )
    real_write = Store._async_write_data
    staged_store = None
    grants = []

    async def fail_rotation(store, envelope):
        nonlocal staged_store
        if ".setup." in store.key and envelope["data"].get("tokens", {}).get("access_token"):
            staged_store = store
            raise WriteError("private-disk-canary")
        await real_write(store, envelope)

    async def provider(client, method, url, **kwargs):
        if method == "POST":
            grants.append(url)
        return await network(client, method, url, **kwargs)

    with (
        patch.object(Store, "_async_write_data", new=fail_rotation),
        patch.object(BrivoClient, "_request", new=provider),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert staged_store is not None
    assert (await disk_data(hass, staged_store))["tokens"].get("refresh_token") != "fake-rotated"
    if cancel:
        hass.config_entries.flow.async_abort(result["flow_id"])
        result = await start(hass, step)
    if step == "manual_token":
        user["client_secret"] = "corrected-secret"
    with patch.object(BrivoClient, "_request", new=provider):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(grants) == 1  # Never reuse the original refresh/invitation.
    assert result["data"]["tokens"]["refresh_token"] == "fake-rotated"
    if step == "manual_token":
        assert result["data"]["application"]["client_secret"] == "corrected-secret"
    assert "private-disk-canary" not in caplog.text


@pytest.mark.parametrize(
    "fault",
    ["silent", "missing", "data", "version", "minor_version", "key", "invalid_json", "read_error"],
)
async def test_verified_save_rejects_actual_disk_mismatch_or_read_failure(hass, fault, caplog):
    store = Store(hass, 1, "synthetic-store-canary", private=True, atomic_writes=True)
    await store.async_save({"tokens": {"refresh_token": "stale-synthetic"}})
    real_write = store._async_write_data
    expected = {"tokens": {"refresh_token": "new-synthetic"}}

    async def corrupt(envelope):
        if fault == "silent":
            return
        if fault == "missing":
            await hass.async_add_executor_job(Path(store.path).unlink)
            return
        if fault in ("invalid_json", "read_error"):
            await real_write(envelope)
            if fault == "invalid_json":
                await hass.async_add_executor_job(
                    Path(store.path).write_text, "invalid-json-canary"
                )
            return
        envelope[fault] = (
            {"tokens": {"refresh_token": "wrong-synthetic"}}
            if fault == "data"
            else "envelope-canary"
        )
        await real_write(envelope)

    real_open = Path.open

    def checked_open(path, *args, **kwargs):
        if str(path) == store.path:
            # Both the disk read and writes must run off the event-loop thread.
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            if fault == "read_error":
                raise PermissionError("private-read-canary")
        return real_open(path, *args, **kwargs)

    caplog.clear()
    with (
        patch.object(store, "_async_write_data", new=corrupt),
        patch.object(Path, "open", new=checked_open),
    ):
        with pytest.raises(BrivoError, match="^Could not save Brivo credentials$") as error:
            await async_save_credentials(store, expected)
    assert error.value.__suppress_context__
    for canary in (
        store.key,
        store.path,
        "private-read-canary",
        "new-synthetic",
        "wrong-synthetic",
        "invalid-json-canary",
    ):
        assert canary not in str(error.value)
        assert canary not in caplog.text


async def test_initial_setup_rotation_survives_swallowed_write_failure_and_retry(hass):
    data = {**DATA, "tokens": {**DATA["tokens"], "expires_at": 0}}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="initial-rotation")
    entry.add_to_hass(hass)
    session = MagicMock()
    session.request.side_effect = [
        Response({"access_token": "new", "refresh_token": "rotated-refresh", "expires_in": 3600}),
        Response({"pass": DATA["credential"]}),
    ]
    real_write = Store._async_write_data

    async def fail_rotation(store, envelope):
        if envelope["data"].get("tokens", {}).get("refresh_token") == "rotated-refresh":
            raise WriteError("private-disk-canary")
        await real_write(store, envelope)

    with (
        patch("custom_components.brivo_mobile_pass.async_get_clientsession", return_value=session),
        patch.object(Store, "_async_write_data", new=fail_rotation),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert dict(entry.data) == data
    with patch("custom_components.brivo_mobile_pass.async_get_clientsession", return_value=session):
        assert await hass.config_entries.async_reload(entry.entry_id)
    assert session.request.call_count == 2
    assert entry.runtime_data.client.tokens["refresh_token"] == "rotated-refresh"
    assert (await disk_data(hass, entry.runtime_data.store))[
        "tokens"
    ] == entry.runtime_data.client.tokens


async def test_cancelled_atomic_writer_error_stays_private_after_helper_exits(hass, caplog):
    import homeassistant.util.file as ha_file

    store = Store(hass, 1, "synthetic-cancel-canary", private=True, atomic_writes=True)
    await async_save_credentials(store, {"tokens": {"refresh_token": "old-synthetic"}})
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    writer_started = asyncio.Event()
    original_write = ha_file.write_utf8_file_atomic

    def held_sync(writer, file):
        started.set()
        loop.call_soon_threadsafe(writer_started.set)
        if not release.wait(timeout=10):
            raise AssertionError("Test did not release the atomic writer")
        raise OSError("private-cancel-os-canary")

    def tracked_write(*args, **kwargs):
        try:
            return original_write(*args, **kwargs)
        finally:
            finished.set()

    caplog.clear()
    with (
        patch.object(ha_file.AtomicWriter, "sync", new=held_sync),
        patch("homeassistant.helpers.storage.write_utf8_file_atomic", new=tracked_write),
    ):
        task = asyncio.create_task(
            async_save_credentials(store, {"tokens": {"refresh_token": "new-synthetic"}})
        )
        try:
            await asyncio.wait_for(writer_started.wait(), timeout=5)
            assert started.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            assert not finished.is_set()  # Real executor work outlives the helper.
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
            await hass.async_block_till_done()
    assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "old-synthetic"
    assert any(record.name == "homeassistant.util.file" for record in caplog.records)
    for canary in (store.key, store.path, "private-cancel-os-canary", "new-synthetic"):
        assert canary not in caplog.text
    await async_save_credentials(store, {"tokens": {"refresh_token": "retry-synthetic"}})
    assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "retry-synthetic"


async def test_concurrent_saves_and_reused_stores_keep_one_durable_filter(hass):
    stores = [
        Store(hass, 1, f"{DOMAIN}.synthetic-dedup", private=True, atomic_writes=True),
        Store(hass, 1, f"{DOMAIN}.synthetic-other", private=True, atomic_writes=True),
    ]
    expected = {"tokens": {"refresh_token": "synthetic-dedup"}}
    await asyncio.gather(*(async_save_credentials(store, expected) for store in stores))
    loggers = [
        logging.getLogger(name)
        for name in ("homeassistant.helpers.storage", "homeassistant.util.file")
    ]
    filters = [
        [redactor for redactor in logger.filters if isinstance(redactor, _PrivateStorageLog)]
        for logger in loggers
    ]
    assert [len(items) for items in filters] == [1, 1]
    redactor = filters[0][0]
    assert filters[1][0] is redactor
    identifiers = set(redactor._identifiers)
    reused = Store(hass, 1, stores[0].key, private=True, atomic_writes=True)
    await asyncio.gather(
        *(async_save_credentials(store, expected) for store in [*stores, stores[0], reused])
    )
    for logger in loggers:
        assert [item for item in logger.filters if isinstance(item, _PrivateStorageLog)] == [
            redactor
        ]
    assert set(redactor._identifiers) == identifiers
    assert {store.key for store in stores} | {store.path for store in stores} <= identifiers
    for store in stores:
        assert await disk_data(hass, store) == expected


async def test_redaction_survives_cross_hass_lifetime_without_hiding_unrelated_logs(
    tmp_path, caplog
):
    import homeassistant.util.file as ha_file
    from pytest_homeassistant_custom_component.common import async_test_home_assistant

    paths = []
    key = f"{DOMAIN}.synthetic-cross-hass"
    for directory in ("first", "second"):
        async with async_test_home_assistant(config_dir=str(tmp_path / directory)) as instance:
            try:
                store = Store(instance, 1, key, private=True, atomic_writes=True)
                await async_save_credentials(store, {"tokens": {"refresh_token": "synthetic"}})
                paths.append(store.path)
                caplog.clear()
                failure = OSError("private-cross-hass-canary")
                with patch.object(ha_file.AtomicWriter, "sync", side_effect=failure):
                    # Exercise both the current path and a previous, stopped HA's
                    # writer path. Neither registration may expire with its hass.
                    for path in paths:
                        with pytest.raises(WriteError) as error:
                            await asyncio.to_thread(
                                ha_file.write_utf8_file_atomic, path, "synthetic"
                            )
                        assert error.value.__cause__ is failure
                        assert str(failure) == "private-cross-hass-canary"
                    for identifier in (key, *paths, "private-cross-hass-canary"):
                        assert identifier not in caplog.text
                    caplog.clear()
                    unrelated = Store(
                        instance, 1, f"{key}-unrelated", private=True, atomic_writes=True
                    )
                    await unrelated.async_save({"synthetic": "unrelated"})
                assert unrelated.key in caplog.text
                assert unrelated.path in caplog.text
                assert "private-cross-hass-canary" in caplog.text
                for name in ("homeassistant.helpers.storage", "homeassistant.util.file"):
                    logging.getLogger(name).warning(
                        "Unrelated storage message: %s", "still-visible"
                    )
                assert caplog.text.count("Unrelated storage message: still-visible") == 2
            finally:
                await instance.async_stop(force=True)


async def test_atomic_os_failure_logs_are_sanitized_and_keep_previous_file(hass, caplog):
    import homeassistant.util.file as ha_file

    store = Store(hass, 1, "synthetic-write-canary", private=True, atomic_writes=True)
    await async_save_credentials(store, {"tokens": {"refresh_token": "old-synthetic"}})
    caplog.clear()
    with patch.object(ha_file.AtomicWriter, "sync", side_effect=OSError("private-os-canary")):
        with pytest.raises(BrivoError) as error:
            await async_save_credentials(store, {"tokens": {"refresh_token": "new-synthetic"}})
    assert error.value.__suppress_context__
    assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "old-synthetic"
    for canary in (store.key, store.path, "private-os-canary", "new-synthetic"):
        assert canary not in caplog.text
        assert canary not in str(error.value)
    # The real writer recovers without weakening privacy after the failure.
    await async_save_credentials(store, {"tokens": {"refresh_token": "new-synthetic"}})
    assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "new-synthetic"


async def test_shutdown_final_write_failure_stays_private_after_helper_exits(hass, caplog):
    import homeassistant.util.file as ha_file
    from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
    from homeassistant.core import CoreState

    store = Store(hass, 1, "synthetic-final-write-canary", private=True, atomic_writes=True)
    await async_save_credentials(store, {"tokens": {"refresh_token": "old-synthetic"}})
    previous_state = hass.state
    hass.set_state(CoreState.stopping)
    caplog.clear()
    try:
        with pytest.raises(BrivoError, match="^Could not save Brivo credentials$"):
            await async_save_credentials(store, {"tokens": {"refresh_token": "new-synthetic"}})
        assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "old-synthetic"
        assert store._unsub_final_write_listener is not None
        with patch.object(
            ha_file.AtomicWriter, "sync", side_effect=OSError("private-final-os-canary")
        ) as sync:
            hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
            await hass.async_block_till_done()
        sync.assert_called_once()
        assert store._unsub_final_write_listener is None
    finally:
        hass.set_state(previous_state)
    assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "old-synthetic"
    assert {"homeassistant.util.file", "homeassistant.helpers.storage"} <= {
        record.name for record in caplog.records if record.levelno == logging.ERROR
    }
    for canary in (store.key, store.path, "private-final-os-canary", "new-synthetic"):
        assert canary not in caplog.text


async def test_shutdown_deferred_save_cannot_confirm_pending_data_as_disk(hass):
    from homeassistant.core import CoreState

    store = Store(hass, 1, "synthetic-shutdown", private=True, atomic_writes=True)
    await async_save_credentials(store, {"tokens": {"refresh_token": "old-synthetic"}})
    previous_state = hass.state
    hass.set_state(CoreState.stopping)
    try:
        with pytest.raises(BrivoError):
            await async_save_credentials(store, {"tokens": {"refresh_token": "new-synthetic"}})
        assert (await disk_data(hass, store))["tokens"]["refresh_token"] == "old-synthetic"
    finally:
        hass.set_state(previous_state)
        await store.async_save({"tokens": {"refresh_token": "new-synthetic"}})


async def test_verified_save_uses_private_atomic_file_and_executor_read(hass):
    entry = await setup(hass)
    store = entry.runtime_data.store
    client = entry.runtime_data.client
    client._dirty = True
    client.tokens["refresh_token"] = "new-synthetic"
    import homeassistant.helpers.storage as ha_storage

    with patch.object(
        ha_storage, "write_utf8_file_atomic", wraps=ha_storage.write_utf8_file_atomic
    ) as atomic:
        await client._save_tokens()
    atomic.assert_called_once()
    assert atomic.call_args.args[2] is True  # Store private flag.
    assert (
        await hass.async_add_executor_job(lambda: Path(store.path).stat().st_mode & 0o777) == 0o600
    )
    assert (await disk_data(hass, store))["tokens"] == client.tokens
