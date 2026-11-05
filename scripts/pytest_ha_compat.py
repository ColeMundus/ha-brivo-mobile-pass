"""Bridge existing helper imports while testing both supported HA harnesses.

Core 2026.11 replaces the discontinued custom-component package with the
official pytest-homeassistant. Only one distribution is installed per uv group;
these aliases reuse its actual helpers, never the old harness against new Core.
Remove this plugin when minimum Core supports the official package too and all
tests can import pytest_homeassistant directly.
"""

import sys
from importlib.util import find_spec


def pytest_configure() -> None:
    """Alias helpers after the official plugin installs its recorder patches."""
    if find_spec("pytest_homeassistant") is not None:
        import pytest_homeassistant
        from pytest_homeassistant import common

        sys.modules["pytest_homeassistant_custom_component"] = pytest_homeassistant
        sys.modules["pytest_homeassistant_custom_component.common"] = common
