"""Credential extraction uses fabricated app values and never contacts Brivo."""

import ast
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.extract_app_credentials import (
    ExtractionError,
    extract_apk,
    extract_from_source,
    save_credentials,
)

SOURCE = """
package com.brivo.pass;
public class Constants {
    enum BuildTypes { RELEASE_PROD("release-fixture"); }
    static class WhenMappings {
        static {
            regions[RegionSelectionState.FIREBASE_EUROPE.ordinal()] = 1;
            regions[RegionSelectionState.FIREBASE_UNITED_STATES.ordinal()] = 2;
            $EnumSwitchMapping$1 = regions;
        }
    }
    public final String getClientId(RegionSelectionState region) {
        int branch = WhenMappings.$EnumSwitchMapping$1[region.ordinal()];
        if (branch == 1) { return "eu-client-fixture"; }
        if (branch != 2) { throw new IllegalStateException(); }
        if (Intrinsics.areEqual("release-fixture", BuildTypes.RELEASE_PROD.getType())) {
            return "us-client-fixture";
        }
        return "debug-client-fixture";
    }
    public final String getClientSecret(RegionSelectionState region) {
        int branch = WhenMappings.$EnumSwitchMapping$1[region.ordinal()];
        if (branch == 1) { return "eu-secret-fixture"; }
        if (branch != 2) { throw new IllegalStateException(); }
        if (Intrinsics.areEqual("release-fixture", BuildTypes.RELEASE_PROD.getType())) {
            return "us-secret-fixture";
        }
        return "debug-secret-fixture";
    }
}
"""
EXPECTED = {"client_id": "us-client-fixture", "client_secret": "us-secret-fixture"}


def test_selects_us_production_not_first_or_debug_values():
    assert extract_from_source(SOURCE) == EXPECTED


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace("FIREBASE_UNITED_STATES", "UNKNOWN_REGION"),
        SOURCE.replace("branch != 2", "branch != 3"),
        SOURCE.replace('areEqual("release-fixture"', 'areEqual("different-build"'),
        SOURCE.replace("getClientSecret", "renamedSecret"),
        SOURCE.replace("if (branch == 1)", "if (branch == 2)"),
        SOURCE.replace("WhenMappings.$EnumSwitchMapping$1", "WhenMappings.$EnumSwitchMapping$0"),
    ],
)
def test_changed_or_unknown_layout_is_rejected(source):
    with pytest.raises(ExtractionError):
        extract_from_source(source)


def test_java_comments_and_braces_in_strings_do_not_change_selection():
    comment = '/* getClientId() { return "wrong"; } */\n'
    source = comment + SOURCE.replace("eu-secret-fixture", "eu-secret-{fixture}")
    assert extract_from_source(source) == EXPECTED


def test_output_is_private_and_existing_file_is_preserved(tmp_path):
    path = tmp_path / "credentials.private.json"
    save_credentials(path, EXPECTED)
    assert json.loads(path.read_text()) == EXPECTED
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_credentials(path, {"client_id": "replacement"})
    assert json.loads(path.read_text()) == EXPECTED


def test_jadx_output_is_temporary_and_logs_are_suppressed(tmp_path, monkeypatch):
    apk = tmp_path / "download.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"fixture")
        archive.writestr("classes.dex", b"fixture")
    monkeypatch.setattr("scripts.extract_app_credentials.shutil.which", lambda name: "/jadx")
    outputs = []

    def run(command, **kwargs):
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 180
        assert command[-1] == str(apk.resolve())
        output = Path(command[command.index("--single-class-output") + 1])
        outputs.append(output)
        output.write_text(SOURCE)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr("scripts.extract_app_credentials.subprocess.run", run)
    assert extract_apk(apk, "jadx") == EXPECTED
    assert not outputs[0].exists()


def test_bundle_is_rejected_with_a_useful_message(tmp_path):
    bundle = tmp_path / "download.xapk"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("base.apk", b"fixture")
    with pytest.raises(ExtractionError, match="plain Android APK"):
        extract_apk(bundle, "jadx")


def test_standalone_helper_supports_python_310_syntax():
    path = Path(__file__).parents[1] / "scripts/extract_app_credentials.py"
    ast.parse(path.read_text(), feature_version=(3, 10))
