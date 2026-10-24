# Releases

The integration is included in the default HACS catalog. No custom-repository registration or APK helper is needed.

1. Update the version in `custom_components/brivo_mobile_pass/manifest.json` and `pyproject.toml`, update `CHANGELOG.md`, then run `uv lock`.
2. Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest`.
3. Review the diff for personal authorization data. Only the explicitly bundled shared app identity belongs in source control; never publish tokens, invitations, pass credentials, APKs, or Home Assistant storage files.
4. Push and wait for the tests, Hassfest, and HACS validation jobs to pass.
5. Publish a GitHub release at the verified commit with the matching version. HACS installs the integration directory from the repository; no separate helper or APK assets are required.
6. Verify the release tag and an installation/update through HACS before claiming live installation has been tested.

Example:

```sh
gh release create v0.2.0 --target <verified-commit> --title '0.2.0' --notes-file release-notes.md
```

Do not replace saved app credentials in existing config entries when updating the bundled profile. Refresh tokens remain bound to the client that issued them.
