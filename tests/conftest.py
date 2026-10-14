"""Enable custom integrations in the real Home Assistant test harness."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield
