# Releases and HACS listing

## Publish a release

1. Update the version in `manifest.json` and `pyproject.toml`, then run `uv lock`.
2. Run the checks in [Development](ARCHITECTURE.md).
3. Push the changes and wait for all GitHub Actions jobs to pass.
4. Test installation through HACS as a custom repository.
5. Create a GitHub release with the matching version. Attach the standalone APK helper so users can download it directly.

For example:

```sh
gh release create v0.1.1 scripts/extract_app_credentials.py --title '0.1.1' --notes-file release-notes.md
```

## Submit to the default HACS catalog

The repository already includes the integration manifest, `hacs.json`, README, license, local brand icon, and HACS/Hassfest validation. GitHub issues and repository topics are enabled.

After publishing a release:

1. Fork [hacs/default](https://github.com/hacs/default) under your personal GitHub account.
2. Create a new branch from its `master` branch.
3. Add `"ColeMundus/ha-brivo-mobile-pass"` to the `integration` JSON list in alphabetical order. Keep the repository name's exact spelling.
4. Open a pull request to `hacs/default` and allow maintainers to edit the branch.
5. Wait for the submission checks and maintainer review. Address any requested changes.

After the pull request is merged, HACS adds the repository during its next catalog scan. Until then, users can install it through Custom repositories.

See the [HACS submission requirements](https://hacs.dev/docs/publish/include/) for the current rules. HACS validation passing on this repository does not mean the catalog submission has been approved.
