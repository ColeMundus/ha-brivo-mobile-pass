# Publish on GitHub and prepare for HACS

The intended repository is `ColeMundus/ha-brivo-mobile-pass`. Code-owner and documentation links already use that name; update them if the name changes.

## Create and push

Authenticate GitHub CLI on a machine you control:

```sh
gh auth login
cd /home/cole/ha-brivo-mobile-pass
gh repo create ColeMundus/ha-brivo-mobile-pass --public --source=. --remote=origin --push --description 'Native Home Assistant controls for Brivo Mobile Pass gates and doors'
gh repo edit --add-topic home-assistant --add-topic hacs --add-topic brivo --enable-issues
```

The local repository contains only public source, fabricated test credentials, docs, and validation configuration. Real credentials and investigation artifacts live outside it. `.gitignore` excludes local environments, private files, tokens, HA storage, keys, and APKs. Review `git ls-files` before publishing; gitignore does not protect already tracked files.

## Validate and release

1. Wait for tests, Ruff, Hassfest, and HACS validation to pass. HACS checks remote metadata, so its complete validation needs the published repository.
2. Verify installation from HACS as a custom repository and test on a second account/site when available.
3. Publish a GitHub release, not merely a tag:

```sh
gh release create v0.1.0 --title 'Brivo Mobile Pass 0.1.0' --notes 'Initial native Home Assistant integration. See README for credential prerequisites and compatibility limits.'
```

Do not call this an approved/default HACS integration until its submission is accepted. The manifest version and release version should agree.

## Default HACS catalog

The repository is structured for HACS, with one integration under `custom_components`, a versioned manifest, `hacs.json`, local brand assets, README, license, issues, and validation actions. Current HACS validator supports local `custom_components/brivo_mobile_pass/brand/icon.png`; older documentation requires a Home Assistant brands entry. If the validator requests upstream branding, submit appropriate assets to home-assistant/brands.

After the published checks and release pass, follow the [official HACS submission instructions](https://hacs.dev/docs/publish/include/) to add this repository to `hacs/default` in alphabetical order. Only the owner or a major contributor can submit. Public GitHub hosting, description, topics, issues, passing validation, and release metadata are remote steps; they cannot be verified before publication.

This integration requires users to supply matching Mobile Pass OAuth application credentials. Decide how to support/document authorized credential provisioning before promoting it widely. No application secret is distributed in the repository.

References: [HACS general requirements](https://hacs.dev/docs/publish/start/), [integration requirements](https://hacs.dev/docs/publish/integration/), and [submission](https://hacs.dev/docs/publish/include/).
