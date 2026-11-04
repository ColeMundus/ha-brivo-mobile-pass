"""Exercise the actual auto-merge policy without network access."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HEAD_SHA = "b" * 40


def signed_commit(sha, **overrides):
    return {
        "sha": sha,
        "author": {"login": "dependabot[bot]", "id": 49699333},
        "commit": {"verification": {"verified": True, "reason": "valid"}},
        **overrides,
    }


def run_verifier(monkeypatch, *, pages=None, before=None, after=None, calls=None):
    steps = load_workflow()["jobs"]["automerge"]["steps"]
    verifier = next((step for step in steps if step.get("id") == "verify_commits"), None)
    assert verifier is not None, "Missing explicit all-commit verifier"
    assert steps.index(verifier) < next(
        index for index, step in enumerate(steps) if step.get("id") == "metadata"
    )
    assert verifier["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    for key, value in {
        "GH_TOKEN": "offline-test-token",
        "GH_REPO": "ColeMundus/ha-brivo-mobile-pass",
        "PR_NUMBER": "7",
        "HEAD_SHA": HEAD_SHA,
    }.items():
        monkeypatch.setenv(key, value)
    pr = {"head": {"sha": HEAD_SHA}, "commits": 2}
    endpoint = "repos/ColeMundus/ha-brivo-mobile-pass/pulls/7"
    responses = iter(
        [
            (["gh", "api", endpoint], pr if before is None else before),
            (
                ["gh", "api", "--paginate", "--slurp", f"{endpoint}/commits?per_page=100"],
                [[signed_commit("a" * 40), signed_commit(HEAD_SHA)]] if pages is None else pages,
            ),
            (["gh", "api", endpoint], pr if after is None else after),
        ]
    )
    calls = [] if calls is None else calls

    def api(command, **kwargs):
        expected_command, response = next(responses)
        assert command == expected_command
        assert not kwargs.get("shell")
        calls.append(command)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response)

    monkeypatch.setattr(subprocess, "check_output", api)
    script = verifier["run"]
    prefix, separator, code = script.partition("python3 - <<'PY'\n")
    assert separator and not prefix.strip() and code.endswith("\nPY\n")
    exec(compile(code.removesuffix("PY\n"), "<workflow-verifier>", "exec"), {})
    assert len(calls) == 3
    return calls


def test_verifier_accepts_multiple_signed_dependabot_commits(monkeypatch):
    run_verifier(monkeypatch)


@pytest.mark.parametrize("count", [101, 250])
def test_verifier_accepts_paginated_signed_dependabot_commits(monkeypatch, count):
    commits = [signed_commit(f"{index:040x}") for index in range(count - 1)]
    commits.append(signed_commit(HEAD_SHA))
    pr = {"head": {"sha": HEAD_SHA}, "commits": count}
    pages = [commits[index : index + 100] for index in range(0, count, 100)]
    run_verifier(monkeypatch, pages=pages, before=pr, after=pr)


@pytest.mark.parametrize("when", ["before", "pages", "after"])
def test_verifier_fails_closed_on_api_error(monkeypatch, when):
    with pytest.raises(subprocess.CalledProcessError):
        run_verifier(monkeypatch, **{when: subprocess.CalledProcessError(1, ["gh", "api"])})


@pytest.mark.parametrize(
    "overrides",
    [
        {"commit": {"verification": {"verified": False, "reason": "unsigned"}}},
        {"commit": {"verification": {"verified": False, "reason": "invalid"}}},
        {"commit": {"verification": {"verified": True, "reason": "invalid"}}},
        {"commit": {"verification": {"verified": "true", "reason": "valid"}}},
        {"commit": {}},
        {"author": {"login": "human", "id": 123}},
        {"author": {"login": "dependabot[bot]", "id": 123}},
        {"author": {"login": "human", "id": 49699333}},
        {"author": None},
    ],
    ids=[
        "unsigned",
        "invalid",
        "invalid-reason",
        "nonboolean-verified",
        "missing-signature",
        "nonbot",
        "wrong-bot-id",
        "wrong-bot-login",
        "missing-author",
    ],
)
@pytest.mark.parametrize("later_page", [False, True], ids=["second-commit", "later-page"])
def test_verifier_rejects_untrusted_commit(monkeypatch, overrides, later_page):
    first = signed_commit("a" * 40)
    second = signed_commit(HEAD_SHA, **overrides)
    pages = [[first], [second]] if later_page else [[first, second]]
    with pytest.raises(SystemExit, match="Untrusted commit"):
        run_verifier(monkeypatch, pages=pages)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("head", [{"sha": "c" * 40}, {}], ids=["changed", "missing"])
def test_verifier_rejects_head_mismatch(monkeypatch, when, head):
    calls = []
    with pytest.raises(SystemExit, match="PR head does not match event head"):
        run_verifier(monkeypatch, calls=calls, **{when: {"head": head, "commits": 2}})
    assert len(calls) == (1 if when == "before" else 3)


@pytest.mark.parametrize(
    ("before_count", "after_count", "enumerated"),
    [
        (3, 3, 2),
        (2, 3, 2),
        (3, 2, 2),
        (1, 1, 2),
        (0, 0, 0),
        (251, 251, 250),
        (251, 251, 251),
        (None, None, 2),
        ("2", "2", 2),
    ],
    ids=[
        "truncated",
        "count-changed-after",
        "count-changed-before",
        "extra-commits",
        "empty",
        "api-cap-truncation",
        "over-api-cap",
        "missing-count",
        "invalid-count",
    ],
)
def test_verifier_rejects_incomplete_enumeration(
    monkeypatch, before_count, after_count, enumerated
):
    commits = [signed_commit(f"{index:040x}") for index in range(enumerated)]
    if commits:
        commits[-1]["sha"] = HEAD_SHA
    pages = [commits[index : index + 100] for index in range(0, len(commits), 100)]
    with pytest.raises(SystemExit, match="Incomplete PR commit enumeration"):
        run_verifier(
            monkeypatch,
            pages=pages,
            before={"head": {"sha": HEAD_SHA}, "commits": before_count},
            after={"head": {"sha": HEAD_SHA}, "commits": after_count},
        )


def test_verifier_rejects_enumeration_not_ending_at_event_head(monkeypatch):
    with pytest.raises(SystemExit, match="Last commit does not match event head"):
        run_verifier(monkeypatch, pages=[[signed_commit("a" * 40), signed_commit("c" * 40)]])


def load_workflow():
    # BaseLoader preserves the GitHub Actions "on" key rather than YAML 1.1 booleans.
    return yaml.load(
        (ROOT / ".github/workflows/dependabot-automerge.yml").read_text(), Loader=yaml.BaseLoader
    )


def run_policy(tmp_path, dependencies):
    workflow = load_workflow()
    policy = next(
        step for step in workflow["jobs"]["automerge"]["steps"] if step.get("id") == "policy"
    )
    output = tmp_path / "output"
    output.unlink(missing_ok=True)
    result = subprocess.run(
        ["bash", "-e", "-c", policy["run"]],
        env={
            **os.environ,
            "DEPENDENCIES": json.dumps(dependencies),
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stderr == ""
    decision = output.read_text()
    assert decision in {"eligible=true\n", "eligible=false\n"}
    return decision == "eligible=true\n"


def dependency(**overrides):
    return {
        "dependencyName": "astral-sh/setup-uv",
        "packageEcosystem": "github_actions",
        "prevVersion": "10.3.0",
        "newVersion": "10.3.3",
        "updateType": "version-update:semver-patch",
        **overrides,
    }


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"newVersion": "10.4.0", "updateType": "version-update:semver-minor"}, True),
        ({"dependencyName": "actions/checkout", "newVersion": "v10.3.3"}, True),
        ({"dependencyName": "dependabot/fetch-metadata"}, True),
        ({"newVersion": "11.0.0", "updateType": "version-update:semver-major"}, False),
        ({"newVersion": "11.0.0"}, False),
        ({"newVersion": "10.3.0"}, False),
        ({"newVersion": "10.2.0"}, False),
        ({"newVersion": "10.3.4-rc.1"}, False),
        ({"prevVersion": ""}, False),
        ({"newVersion": ""}, False),
        ({"updateType": ""}, False),
        ({"dependencyName": "unknown/action"}, False),
        ({"packageEcosystem": "pip"}, False),
        ({"maintainerChanges": True}, False),
        ({"dependencyName": "home-assistant/actions/hassfest", "newVersion": "a" * 40}, True),
        ({"dependencyName": "hacs/action", "newVersion": "b" * 40}, True),
        ({"dependencyName": "hacs/action", "newVersion": "v2.0.0"}, False),
        ({"dependencyName": "hacs/action", "newVersion": "b" * 39}, False),
        ({"dependencyName": "hacs/action", "newVersion": "z" * 40}, False),
        ({"newVersion": "a" * 40}, False),
    ],
)
def test_update_policy(tmp_path, overrides, expected):
    assert run_policy(tmp_path, [dependency(**overrides)]) is expected


def test_empty_metadata_is_not_eligible(tmp_path):
    assert not run_policy(tmp_path, [])


def test_group_requires_every_dependency_to_be_eligible(tmp_path):
    assert run_policy(tmp_path, [dependency(), dependency(dependencyName="actions/checkout")])
    assert not run_policy(tmp_path, [dependency(), dependency(newVersion="11.0.0")])


def test_privileged_workflow_never_executes_pull_request_code():
    workflow = load_workflow()
    assert set(workflow["on"]) == {"pull_request_target"}
    job = workflow["jobs"]["automerge"]
    for guard in (
        "github.repository == 'ColeMundus/ha-brivo-mobile-pass'",
        "github.event.pull_request.user.login == 'dependabot[bot]'",
        "github.event.pull_request.head.repo.full_name == github.repository",
        "github.event.pull_request.base.ref == 'main'",
        "!github.event.pull_request.draft",
    ):
        assert guard in job["if"]
    metadata = next(step for step in job["steps"] if step.get("id") == "metadata")
    assert re.fullmatch(r"dependabot/fetch-metadata@[0-9a-f]{40}", metadata["uses"])
    assert "with" not in metadata  # Signature/author verification must not be disabled.
    assert all("actions/checkout" not in step.get("uses", "") for step in job["steps"])
    merge = job["steps"][-1]
    assert merge["if"] == "steps.policy.outputs.eligible == 'true'"
    assert '--auto --squash --match-head-commit "$HEAD_SHA" "$PR_URL"' in merge["run"]
    assert "--admin" not in merge["run"]
