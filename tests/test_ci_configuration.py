"""Keep minimum/latest Core coverage behind the existing required check."""

import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from itertools import product
from pathlib import Path

import pytest
import yaml

from scripts import check_ha_test_versions

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/validate.yml"


def test_ci_covers_exact_minimum_and_latest_core_with_matching_harnesses():
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    compatibility = jobs["compatibility"]
    assert compatibility["strategy"]["fail-fast"] is False
    assert compatibility["strategy"]["matrix"]["include"] == [
        {
            "lane": "minimum",
            "group": "dev",
            "core": "2026.10.0",
            "harness": "pytest-homeassistant-custom-component",
            "harness-version": "0.13.371",
        },
        {
            "lane": "latest",
            "group": "ci-latest",
            "core": "2026.11.0",
            "harness": "pytest-homeassistant",
            "harness-version": "2026.11.0",
        },
    ]
    commands = "\n".join(step.get("run", "") for step in compatibility["steps"])
    assert "uv sync --locked --no-default-groups --group" in commands
    assert "uv run --no-sync python scripts/check_ha_test_versions.py" in commands
    assert "uv run --no-sync pytest" in commands
    assert "continue-on-error" not in compatibility
    assert not any(step.get("continue-on-error") for step in compatibility["steps"])


@pytest.mark.parametrize(
    ("compatibility", "actionlint"),
    product(("success", "failure", "cancelled", "skipped"), repeat=2),
)
def test_required_tests_gate_rejects_any_unsuccessful_dependency(compatibility, actionlint):
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    gate = jobs["tests"]
    assert gate.get("name", "tests") == "tests"
    assert set(gate["needs"]) == {"compatibility", "actionlint"}
    assert gate["if"] == "${{ always() }}"
    assert "strategy" not in gate
    assert "continue-on-error" not in gate
    assert len(gate["steps"]) == 1
    step = gate["steps"][0]
    assert step["env"] == {
        "COMPATIBILITY_RESULT": "${{ needs.compatibility.result }}",
        "ACTIONLINT_RESULT": "${{ needs.actionlint.result }}",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        env={"COMPATIBILITY_RESULT": compatibility, "ACTIONLINT_RESULT": actionlint},
        check=False,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is (compatibility == actionlint == "success")


def test_actionlint_is_checksum_pinned_and_checks_every_workflow():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert workflow["permissions"] == {"contents": "read"}
    lint = workflow["jobs"]["actionlint"]
    assert "permissions" not in lint
    assert "continue-on-error" not in lint
    commands = "\n".join(step.get("run", "") for step in lint["steps"])
    assert "https://github.com/rhysd/actionlint/releases/download/v1.7.12/" in commands
    assert "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8" in commands
    assert commands.index("sha256sum --check") < commands.index("tar -xzf")
    assert '"$RUNNER_TEMP/actionlint" -color -pyflakes ""' in commands
    # No file arguments: actionlint discovers all .yml/.yaml workflows, including
    # pull_request_target automation. Shellcheck stays enabled when available.
    assert commands.rstrip().endswith('"$RUNNER_TEMP/actionlint" -color -pyflakes ""')
    assert not any(step.get("continue-on-error") for step in lint["steps"])


def test_pytest_config_supports_official_harness_and_keeps_legacy_helpers():
    config = tomllib.loads((WORKFLOW.parents[2] / "pyproject.toml").read_text())
    options = config["tool"]["pytest"]["ini_options"]
    assert options["addopts"] == "-p no:respx -p scripts.pytest_ha_compat"
    assert options["asyncio_default_fixture_loop_scope"] == "function"


@pytest.mark.parametrize(
    ("installed", "expected_error"),
    [
        ({"homeassistant": "2026.10.1"}, "Expected homeassistant==2026.10.0"),
        (
            {"pytest-homeassistant-custom-component": "0.13.372"},
            "Expected pytest-homeassistant-custom-component==0.13.371",
        ),
        ({"pytest-homeassistant": "2026.11.0"}, "Conflicting harness installed"),
        ({}, None),
    ],
)
def test_version_check_rejects_wrong_pins_and_overlapping_harnesses(
    monkeypatch, installed, expected_error
):
    distributions = {
        "homeassistant": "2026.10.0",
        "pytest-homeassistant-custom-component": "0.13.371",
        **installed,
    }

    def version(package):
        if package not in distributions:
            raise PackageNotFoundError(package)
        return distributions[package]

    monkeypatch.setattr(check_ha_test_versions, "version", version)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_ha_test_versions.py",
            "2026.10.0",
            "pytest-homeassistant-custom-component",
            "0.13.371",
        ],
    )
    if expected_error:
        with pytest.raises(SystemExit, match=expected_error):
            check_ha_test_versions.main()
    else:
        check_ha_test_versions.main()
