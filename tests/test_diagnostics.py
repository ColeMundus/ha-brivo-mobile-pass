"""Allowlisted diagnostics with private-data canaries and no provider traffic."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brivo_mobile_pass.api import BrivoClient, BrivoError
from custom_components.brivo_mobile_pass.const import DOMAIN
from custom_components.brivo_mobile_pass.diagnostics import async_get_config_entry_diagnostics


async def test_loaded_diagnostics_use_live_snapshot_and_only_allowlisted_telemetry(hass, caplog):
    caplog.set_level("DEBUG", logger="custom_components.brivo_mobile_pass")
    canary = "PRIVATE-canary-token-url-id-name-location-message-body"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=canary,
        unique_id=canary,
        data={
            "application": {"client_id": canary, "client_secret": canary},
            "credential": canary,
            "access_points": [{"id": canary, "name": canary, "site_name": canary}],
            "tokens": {"access_token": canary, "refresh_token": canary},
            "unexpected": canary,
        },
    )
    api = BrivoClient(MagicMock(), entry.data["application"], canary, [], {}, AsyncMock())
    api._last_successful_request_at = 123.5
    api._last_error = "auth"
    api._last_error_at = 124.5
    entry.runtime_data = SimpleNamespace(client=api, private=canary)
    with patch(
        "custom_components.brivo_mobile_pass.diagnostics.async_get_integration",
        return_value=SimpleNamespace(version="1.2.3"),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result == {
        "integration_version": "1.2.3",
        "loaded": True,
        "access_point_count": 0,
        "credential_present": True,
        "last_successful_request_at": 123.5,
        "last_error": "auth",
        "last_error_at": 124.5,
    }
    assert canary not in json.dumps(result)
    assert canary not in caplog.text
    api.session.request.assert_not_called()


@pytest.mark.parametrize("runtime", ["absent", None, SimpleNamespace(client=None)])
async def test_unloaded_diagnostics_have_no_stale_telemetry(hass, runtime):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"credential": "credential-canary", "access_points": [{"id": "id-canary"}]},
    )
    if runtime != "absent":
        entry.runtime_data = runtime
    with patch(
        "custom_components.brivo_mobile_pass.diagnostics.async_get_integration",
        return_value=SimpleNamespace(version="1.2.3"),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result == {
        "integration_version": "1.2.3",
        "loaded": False,
        "access_point_count": 1,
        "credential_present": True,
        "last_successful_request_at": None,
        "last_error": None,
        "last_error_at": None,
    }
    assert "canary" not in json.dumps(result)


async def test_real_failure_diagnostics_and_debug_logs_exclude_provider_canaries(hass, caplog):
    caplog.set_level("DEBUG", logger="custom_components.brivo_mobile_pass")
    canary = "https://private-canary.invalid/secret-token-canary"
    session = MagicMock()
    session.request.side_effect = TimeoutError(canary)
    api = BrivoClient(
        session,
        {"client_id": canary, "client_secret": canary},
        canary,
        [{"id": canary, "name": canary, "site_name": canary}],
        {"access_token": canary, "refresh_token": canary, "expires_at": 9999999999},
        AsyncMock(),
    )
    with pytest.raises(BrivoError) as error:
        await api.check_connection()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = SimpleNamespace(client=api)
    with patch(
        "custom_components.brivo_mobile_pass.diagnostics.async_get_integration",
        return_value=SimpleNamespace(version="1.2.3"),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["last_error"] == "timeout"
    assert isinstance(result["last_error_at"], float)
    assert result["last_successful_request_at"] is None
    assert "canary" not in json.dumps(result)
    assert "canary" not in str(error.value)
    assert "canary" not in caplog.text
    assert "Brivo operation failed (timeout)" in caplog.text
    assert session.request.call_count == 1
