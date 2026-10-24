"""Bundled app identity has explicit, reproducible provenance; no pass data."""

from importlib import util
from pathlib import Path
from uuid import UUID


def test_bundled_application_profile():
    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components/brivo_mobile_pass/app_credentials.py"
    )
    assert path.is_file(), "The default app profile must ship with HA"
    spec = util.spec_from_file_location("bundled_app_profile", path)
    assert spec is not None and spec.loader is not None
    profile = util.module_from_spec(spec)
    spec.loader.exec_module(profile)
    assert profile.APP_CREDENTIALS_PROFILE == "mobile-pass-4.33.2-us-production"
    assert len(profile.SOURCE_APK_SHA256) == 64
    assert int(profile.SOURCE_APK_SHA256, 16) > 0
    assert set(profile.DEFAULT_APPLICATION) == {"client_id", "client_secret"}
    assert (
        str(UUID(profile.DEFAULT_APPLICATION["client_id"]))
        == profile.DEFAULT_APPLICATION["client_id"]
    )
    assert len(profile.DEFAULT_APPLICATION["client_secret"]) >= 16
