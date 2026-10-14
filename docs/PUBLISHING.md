# Releases

1. Update the version in `manifest.json` and `pyproject.toml`.
2. Run the checks listed in [Development](ARCHITECTURE.md).
3. Push the changes and wait for GitHub Actions to pass.
4. Test installation through HACS.
5. Create a GitHub release with the matching version and a description of the changes.

For example:

```sh
gh release create v0.1.0 --title '0.1.0' --notes-file release-notes.md
```

HACS users can install this repository through **Custom repositories**. To submit it to the default catalog, follow the [HACS submission guide](https://hacs.dev/docs/publish/include/) after publishing a release.
