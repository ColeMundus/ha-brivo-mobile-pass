"""Offline coverage of the normal and advanced setup paths."""

import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brivo_mobile_pass.api import AUTH_URL, BrivoClient, BrivoError, HttpFailure
from custom_components.brivo_mobile_pass.app_credentials import DEFAULT_APPLICATION
from custom_components.brivo_mobile_pass.const import DOMAIN


def test_translations_match_forms_and_explain_default_scope():
    root = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    strings = json.loads((root / "strings.json").read_text())
    assert strings == json.loads((root / "translations/en.json").read_text())
    steps = strings["config"]["step"]
    assert list(steps["user"]["menu_options"]) == ["invitation", "token", "advanced"]
    assert list(steps["advanced"]["menu_options"]) == ["manual_invitation", "manual_token"]
    assert set(steps["invitation"]["data"]) == {"email", "invitation_code"}
    assert set(steps["token"]["data"]) == {"refresh_token"}
    for mode in ("invitation", "token"):
        assert set(steps[f"manual_{mode}"]["data"]) == (
            set(steps[mode]["data"]) | {"client_id", "client_secret"}
        )
        assert "US" in steps[mode]["description"]
        assert "saved" in steps[mode]["description"]
    assert "unused" in steps["invitation"]["description"]
    assert "Advanced" in strings["config"]["error"]["unsupported_client"]


@pytest.fixture(autouse=True)
def fake_default_application(monkeypatch):
    """Use fictional shared credentials even at the fake HTTP boundary."""
    monkeypatch.setitem(DEFAULT_APPLICATION, "client_id", "fake-us-client")
    monkeypatch.setitem(DEFAULT_APPLICATION, "client_secret", "fake-us-secret")


@pytest.mark.parametrize("token", ["opaque-token", "x.not-json.y", "x.!.y"])
async def test_unreadable_token_hint_still_uses_brivo_validation(hass, token):
    result = await start(hass, "token")
    with patch.object(BrivoClient, "_request", new=network):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"refresh_token": token}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("reauth", [False, True])
async def test_matching_token_hint_uses_selected_application(hass, reauth):
    application = {"client_id": "legacy-client", "client_secret": "legacy-secret"}
    entry = None
    if reauth:
        entry = MockConfigEntry(domain=DOMAIN, data={"application": application}, unique_id="old")
        entry.add_to_hass(hass)
    else:
        application = dict(DEFAULT_APPLICATION)
    result = await start(hass, "token", entry)
    seen = []

    async def recording(client, method, url, **kwargs):
        if url == AUTH_URL:
            seen.append(kwargs["headers"]["Authorization"])
        return await network(client, method, url, **kwargs)

    with patch.object(BrivoClient, "_request", new=recording):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"refresh_token": jwt({"client_id": application["client_id"]})}
        )
        await hass.async_block_till_done()
    expected = base64.b64encode(
        f"{application['client_id']}:{application['client_secret']}".encode()
    ).decode()
    assert seen == ["Basic " + expected]
    if reauth:
        assert result["reason"] == "reauth_successful"
    else:
        assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("manual", [False, True])
async def test_token_retry_recovers_rotation_and_manual_secret_correction(hass, manual):
    result = await start(hass, "manual_token" if manual else "token")
    user = {"refresh_token": "original-token"}
    if manual:
        user.update(client_id="manual-client", client_secret="old-secret")

    async def failing(client, method, url, **kwargs):
        if url == AUTH_URL:
            return await network(client, method, url, **kwargs)
        raise BrivoError("offline")

    with patch.object(BrivoClient, "_request", new=failing):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["errors"] == {"base": "cannot_connect"}
    hass.config_entries.flow.async_abort(result["flow_id"])
    result = await start(hass, "manual_token" if manual else "token")
    if manual:
        user["client_secret"] = "corrected-secret"

    async def recovered(client, method, url, **kwargs):
        assert client.tokens["refresh_token"] == "fake-rotated"
        if manual:
            assert client.application["client_secret"] == "corrected-secret"
        assert url != AUTH_URL
        return await network(client, method, url, **kwargs)

    with patch.object(BrivoClient, "_request", new=recovered):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("payload", [{}, {"client_id": None}])
async def test_jwt_without_client_hint_still_requires_brivo_validation(hass, payload):
    result = await start(hass, "token")
    with patch.object(
        BrivoClient, "_request", new=AsyncMock(side_effect=HttpFailure(401))
    ) as request:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"refresh_token": jwt(payload)}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    request.assert_awaited_once()


async def test_manual_token_can_correct_rejected_secret(hass):
    result = await start(hass, "manual_token")
    user = {
        "client_id": "manual-client",
        "client_secret": "incorrect",
        "refresh_token": "fake-token",
    }
    with patch.object(BrivoClient, "_request", new=AsyncMock(side_effect=HttpFailure(401))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["errors"] == {"base": "invalid_auth"}
    assert result["step_id"] == "manual_token"
    user["client_secret"] = "corrected"

    async def corrected(client, method, url, **kwargs):
        assert client.application == {"client_id": "manual-client", "client_secret": "corrected"}
        if url == AUTH_URL:
            assert kwargs["params"]["refresh_token"] == "fake-token"
        return await network(client, method, url, **kwargs)

    with patch.object(BrivoClient, "_request", new=corrected):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_uncertain_invitation_survives_cancellation(hass):
    result = await start(hass, "invitation")
    user = {"email": "test@example.invalid", "invitation_code": "uncertain-code"}
    with patch.object(
        BrivoClient, "_request", new=AsyncMock(side_effect=BrivoError("timeout"))
    ) as request:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        assert result["errors"] == {"base": "cannot_connect"}
        hass.config_entries.flow.async_abort(result["flow_id"])
        result = await start(hass, "invitation")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["reason"] == "invitation_uncertain"
    request.assert_awaited_once()


@pytest.mark.parametrize("reauth", [False, True])
async def test_normal_token_cannot_duplicate_another_entry(hass, reauth):
    unique_id = hashlib.sha256(b"fake-credential").hexdigest()
    existing_data = {
        "application": {"client_id": "existing-client", "client_secret": "existing-secret"},
        "credential": "fake-credential",
    }
    existing = MockConfigEntry(domain=DOMAIN, data=existing_data, unique_id=unique_id)
    existing.add_to_hass(hass)
    entry = None
    if reauth:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="another-pass",
            data={"application": dict(DEFAULT_APPLICATION), "credential": "another-pass"},
        )
        entry.add_to_hass(hass)
    result = await start(hass, "token", entry)
    with patch.object(BrivoClient, "_request", new=network):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"refresh_token": "duplicate-token"}
        )
    assert result["reason"] == "already_configured"
    assert existing.data == existing_data
    if entry:
        assert entry.data["credential"] == "another-pass"
    fingerprint = hashlib.sha256(
        f"{DEFAULT_APPLICATION['client_id']}::duplicate-token".encode()
    ).hexdigest()
    staged = await Store(hass, 1, f"{DOMAIN}.setup.{fingerprint}").async_load()
    assert staged["tokens"]["refresh_token"] == "fake-rotated"


async def start(hass, step, entry=None):
    context = {"source": "user"}
    if entry:
        context = {"source": "reauth", "entry_id": entry.entry_id}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context=context, data=entry.data if entry else None
    )
    if step.startswith("manual_"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "advanced"}
        )
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": step})


@pytest.mark.parametrize("step", ["invitation", "token"])
async def test_normal_reauth_keeps_saved_application(hass, step):
    application = {
        "client_id": "legacy-client",
        "client_secret": "legacy-secret",
        "client_secrets": {"legacy-client": "legacy-secret"},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="legacy-id",
        data={"application": application, "credential": "old-pass", "access_points": []},
    )
    entry.add_to_hass(hass)
    result = await start(hass, step, entry)
    seen = []

    async def recording_network(client, method, url, **kwargs):
        seen.append(dict(client.application))
        return await network(client, method, url, **kwargs)

    user = (
        {"refresh_token": "fake-refresh"}
        if step == "token"
        else {"email": "test@example.invalid", "invitation_code": "fake-code"}
    )
    with patch.object(BrivoClient, "_request", new=recording_network):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert seen and all(app == application for app in seen)
    assert entry.data["application"] == application
    assert entry.runtime_data.client.tokens["refresh_token"] == "fake-rotated"


@pytest.mark.parametrize("cancel", [False, True])
async def test_normal_invitation_recovery_preserves_staged_application(hass, monkeypatch, cancel):
    original = dict(DEFAULT_APPLICATION)
    user = {"email": "test@example.invalid", "invitation_code": "recover-code"}
    result = await start(hass, "invitation")
    grants = []

    async def failing_network(client, method, url, **kwargs):
        if url == AUTH_URL:
            grants.append(kwargs["data"]["grant_type"])
            return await network(client, method, url, **kwargs)
        raise BrivoError("offline")

    with patch.object(BrivoClient, "_request", new=failing_network):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["errors"] == {"base": "cannot_connect"}
    if cancel:
        hass.config_entries.flow.async_abort(result["flow_id"])
        result = await start(hass, "invitation")
    # A later release's default secret must not replace saved credentials.
    monkeypatch.setitem(DEFAULT_APPLICATION, "client_secret", "changed-default-secret")
    seen = []

    async def recovered_network(client, method, url, **kwargs):
        seen.append(dict(client.application))
        assert client.tokens["refresh_token"] == "fake-rotated"
        assert url != AUTH_URL  # Valid staged access token: no redemption or refresh.
        return await network(client, method, url, **kwargs)

    with patch.object(BrivoClient, "_request", new=recovered_network):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert grants == ["invitation"]
    assert seen and all(app == original for app in seen)
    assert result["data"]["application"] == original


def jwt(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{encoded}.fake-signature"


@pytest.mark.parametrize("reauth", [False, True])
async def test_token_client_mismatch_is_local_and_actionable(hass, reauth):
    entry = None
    if reauth:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="old-id",
            data={"application": {"client_id": "old-client", "client_secret": "old-secret"}},
        )
        entry.add_to_hass(hass)
    result = await start(hass, "token", entry)
    token = jwt({"client_id": DEFAULT_APPLICATION["client_id"] if reauth else "foreign-client"})
    with patch.object(BrivoClient, "_request", new=AsyncMock()) as request:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"refresh_token": token}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "token"
    assert result["errors"] == {"base": "unsupported_client"}
    request.assert_not_awaited()
    client_id = "old-client" if reauth else DEFAULT_APPLICATION["client_id"]
    fingerprint = hashlib.sha256(f"{client_id}::{token}".encode()).hexdigest()
    assert await Store(hass, 1, f"{DOMAIN}.setup.{fingerprint}").async_load() is None


async def network(client, method, url, **kwargs):
    """Fake only the HTTP boundary, exercising token and discovery logic."""
    if url == AUTH_URL:
        return {"access_token": "fake-access", "refresh_token": "fake-rotated", "expires_in": 300}
    if url.endswith("/pass"):
        return {"pass": "fake-credential"}
    if "/access-points?" in url:
        return {"data": [{"id": "1", "name": "Gate"}], "count": 1}
    return {"data": [{"id": "2", "name": "Site"}], "count": 1}


async def test_advanced_manual_invitation(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "advanced"}
    )
    assert result["menu_options"] == ["manual_invitation", "manual_token"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual_invitation"}
    )
    assert set(result["data_schema"].schema) == {
        "client_id",
        "client_secret",
        "email",
        "invitation_code",
    }
    application = {"client_id": "manual-client", "client_secret": "manual-secret"}
    with patch.object(BrivoClient, "_request", new=network):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**application, "email": "test@example.invalid", "invitation_code": "manual-code"},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["application"] == application


async def test_normal_invitation_form_needs_only_invitation(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "invitation"}
    )
    assert result["type"] is FlowResultType.FORM
    assert set(result["data_schema"].schema) == {"email", "invitation_code"}


async def test_normal_invitation_uses_copied_default_application(hass):
    expected = dict(DEFAULT_APPLICATION)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "invitation"}
    )
    with patch.object(BrivoClient, "_request", new=network):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "test@example.invalid", "invitation_code": "fake-code"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["application"] == expected
    assert result["data"]["application"] is not DEFAULT_APPLICATION
    assert DEFAULT_APPLICATION == expected


@pytest.mark.parametrize("manual", [False, True])
async def test_token_forms_and_resulting_application(hass, manual):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    if manual:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "advanced"}
        )
    step = "manual_token" if manual else "token"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    application = (
        {"client_id": "other-client", "client_secret": "other-secret"}
        if manual
        else dict(DEFAULT_APPLICATION)
    )
    assert set(result["data_schema"].schema) == (
        {"client_id", "client_secret", "refresh_token"} if manual else {"refresh_token"}
    )
    user = {"refresh_token": jwt({"client_id": application["client_id"]})}
    if manual:
        user.update(application)
    with patch.object(BrivoClient, "_request", new=network):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["application"] == application
