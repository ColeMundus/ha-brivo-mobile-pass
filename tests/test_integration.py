"""Exercise config flows, storage, lifecycle, and entities in Home Assistant."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brivo_mobile_pass.api import BrivoAuthError, BrivoError
from custom_components.brivo_mobile_pass.const import DOMAIN
from custom_components.brivo_mobile_pass.diagnostics import async_get_config_entry_diagnostics

DATA = {
    "application": {"client_id": "test-client", "client_secret": "test-secret"},
    "credential": "test-credential",
    "access_points": [
        {"id": "101", "name": "Example gate", "site_id": "10", "site_name": "Example site"}
    ],
    "tokens": {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "expires_at": 9999999999,
    },
}


async def setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="test-unique")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_button_storage_unload(hass):
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert "tokens" not in entry.data
    stored = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load()
    assert stored["tokens"]["refresh_token"] == "test-refresh"
    buttons = [s for s in hass.states.async_all("button")]
    assert len(buttons) == 1
    with patch.object(entry.runtime_data.client, "open_access_point", new=AsyncMock()) as unlock:
        await hass.services.async_call(
            "button", "press", {"entity_id": buttons[0].entity_id}, blocking=True
        )
        unlock.assert_awaited_once_with("101")
    with patch.object(entry.runtime_data.client, "check_connection", new=AsyncMock()) as check:
        await hass.services.async_call(DOMAIN, "check_connection", {}, blocking=True)
        check.assert_awaited_once()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["access_point_count"] == 1
    assert "test-" not in str(diagnostics)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.tokens").async_load() is None


@pytest.mark.parametrize(
    "error,state",
    [
        (BrivoAuthError("revoked"), ConfigEntryState.SETUP_ERROR),
        (BrivoError("offline"), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_error_mapping(hass, error, state):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="test-unique")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection",
        new=AsyncMock(side_effect=error),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is state


async def test_button_failure_surfaces_and_starts_reauth(hass):
    entry = await setup(hass)
    entity_id = hass.states.async_all("button")[0].entity_id
    with (
        patch.object(
            entry.runtime_data.client,
            "open_access_point",
            new=AsyncMock(side_effect=BrivoAuthError("revoked")),
        ),
        patch.object(type(entry), "async_start_reauth") as reauth,
    ):
        with pytest.raises(HomeAssistantError, match="revoked"):
            await hass.services.async_call(
                "button", "press", {"entity_id": entity_id}, blocking=True
            )
        reauth.assert_called_once()


async def test_user_menu_and_invalid_import(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["token", "invitation"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "import"}, data={}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_import"


async def test_import_and_duplicate(hass):
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data=DATA
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "import"}, data=DATA
    )
    assert result["reason"] == "already_configured"


async def test_invitation_saved_once_even_if_discovery_fails(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "invitation"}
    )
    user = {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "email": "example@example.invalid",
        "invitation_code": "test-code",
    }

    async def redeem(client, *args):
        await client._accept_tokens(
            {"access_token": "new", "refresh_token": "rotated", "expires_in": 300}
        )

    async def discover(client):
        client.credential = "test-credential"
        client.access_points = DATA["access_points"]

    with (
        patch("custom_components.brivo_mobile_pass.api.BrivoClient.redeem_invitation", new=redeem),
        patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.discover",
            new=AsyncMock(side_effect=BrivoError("offline")),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["errors"] == {"base": "cannot_connect"}
    with (
        patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.redeem_invitation", new=AsyncMock()
        ) as redeem_again,
        patch("custom_components.brivo_mobile_pass.api.BrivoClient.discover", new=discover),
        patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
        ),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    redeem_again.assert_not_awaited()


async def test_uncertain_invitation_is_not_redeemed_twice(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "invitation"}
    )
    user = {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "email": "example@example.invalid",
        "invitation_code": "test-code",
    }
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.redeem_invitation",
        new=AsyncMock(side_effect=BrivoError("timeout")),
    ) as redeem:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["reason"] == "invitation_uncertain"
    redeem.assert_awaited_once()


async def test_reauth_replaces_tokens_and_updates_entry(hass):
    entry = await setup(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )

    async def discover(client):
        client.credential = "replacement-credential"
        client.access_points = DATA["access_points"]

    with (
        patch("custom_components.brivo_mobile_pass.api.BrivoClient.discover", new=discover),
        patch(
            "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "client_id": "test-client",
                "client_secret": "test-secret",
                "refresh_token": "replacement-refresh",
            },
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.runtime_data.client.tokens["refresh_token"] == "replacement-refresh"
    assert entry.data["credential"] == "replacement-credential"


async def test_restart_with_old_bootstrap_preserves_rotated_tokens(hass):
    entry = await setup(hass)
    client = entry.runtime_data.client
    client.tokens["refresh_token"] = "rotated-refresh"
    client._dirty = True
    await client._save_tokens()
    assert await hass.config_entries.async_unload(entry.entry_id)
    # Simulate a restart before HA flushed removal of the bootstrap entry data.
    hass.config_entries.async_update_entry(entry, data=DATA)
    with patch(
        "custom_components.brivo_mobile_pass.api.BrivoClient.check_connection", new=AsyncMock()
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.client.tokens["refresh_token"] == "rotated-refresh"
