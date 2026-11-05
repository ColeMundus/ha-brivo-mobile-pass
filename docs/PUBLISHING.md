# Releases

The integration is included in the default HACS catalog. No custom-repository registration or APK helper is needed.

1. Update the version in `custom_components/brivo_mobile_pass/manifest.json` and `pyproject.toml`, update `CHANGELOG.md`, then run `uv lock`.
2. Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest`. Verify every Home Assistant compatibility lane and Actionlint in CI, not only the default local test environment.
3. Review the diff for personal authorization data. Only the explicitly bundled shared app identity belongs in source control; never publish tokens, invitations, pass credentials, APKs, or Home Assistant storage files.
4. Push and wait for the tests, Hassfest, and HACS validation jobs to pass.
5. Publish a GitHub release at the verified commit with the matching version. HACS installs the integration directory from the repository; no separate helper or APK assets are required.
6. Verify the release tag and an installation/update through HACS before claiming live installation has been tested. Follow the [safe live-check procedure](TESTING.md); a physical unlock is a separate, explicitly authorized check.

Example:

```sh
gh release create v0.3.0 --target <verified-commit> --title '0.3.0' --notes-file release-notes.md
```

Do not replace saved app credentials in existing config entries when updating the bundled profile. Refresh tokens remain bound to the client that issued them.

## Dependency maintenance

Dependabot checks GitHub Actions monthly. The auto-merge workflow verifies that
updates contain only signed Dependabot commits and come from this repository.
Before fetching metadata, it paginates every PR commit and requires the
`dependabot[bot]` author login and ID (`49699333`) plus a valid, verified signature
on each commit. It checks the current PR head before and after enumeration against
the event head, requires the final commit to match that head, and checks the full
commit count. Incomplete results and PRs over the API's 250-commit limit fail closed
and need manual review. The merge command also requires the same event head.
It enables squash auto-merge for patch/minor updates to `actions/checkout`,
`astral-sh/setup-uv`, and `dependabot/fetch-metadata`, plus SHA updates to the
tracked `home-assistant/actions/hassfest` and `hacs/action` branches. Major
versions, prereleases, unknown dependencies, and maintainer-change notices need
manual review. The privileged workflow never checks out pull request code.

Repository settings must keep auto-merge enabled and protect `main` with the
GitHub Actions `tests`, `hassfest`, and `hacs` checks required before merging.
Auto-merge does not bypass these checks. Keep branches up to date before merging;
Use `@dependabot rebase` so updates retain signed Dependabot commits rather than
introducing a maintainer-authored merge commit that the verifier will reject.
Adding a label to an existing Dependabot PR reruns eligibility checks without
bypassing this policy. No personal access token or automatic review approval is
needed.
