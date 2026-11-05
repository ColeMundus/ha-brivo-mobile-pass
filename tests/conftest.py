"""Enable custom integrations in the real Home Assistant test harness."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def hass_storage():
    """Keep real Store I/O: credential success requires filesystem readback."""
    yield {}


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir):
    """Isolate real storage under the harness's temporary config directory."""
    return hass_tmp_config_dir
