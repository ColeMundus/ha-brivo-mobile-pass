"""Fail CI if dependency selection differs from the declared compatibility lane."""

import sys
from importlib.metadata import PackageNotFoundError, version


def main() -> None:
    """Verify both exact pins, and reject overlapping pytest harness plugins."""
    core, harness, harness_version = sys.argv[1:]
    for package, expected in (("homeassistant", core), (harness, harness_version)):
        installed = version(package)
        print(f"{package}=={installed}", flush=True)
        if installed != expected:
            raise SystemExit(f"Expected {package}=={expected}; installed {installed}")
    other = (
        "pytest-homeassistant"
        if harness == "pytest-homeassistant-custom-component"
        else "pytest-homeassistant-custom-component"
    )
    try:
        version(other)
    except PackageNotFoundError:
        return
    raise SystemExit(f"Conflicting harness installed: {other}")


if __name__ == "__main__":
    main()
