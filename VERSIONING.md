# HardAtlas version management

## Current authority

The existing source/package values below are preserved. They are not a claim that a product is stable or that these source commits were released. Independent applications, libraries, build tools and protocol/schema numbers are not reset to one repository-wide version.

- `rust-workspace`: `0.1.0` from `Cargo.toml` (workspace.package.version); explicit local bump supported. Rust workspace package version; inherited crate declarations remain source bindings Tauri application packaging version is synchronized with its own Cargo package.
- `node-apps-admin`: `0.1.0` from `apps/admin/package.json` (/version); explicit local bump supported. Node package version; independent unless explicitly coupled by the application package below.
- `node-apps-web`: `0.1.0` from `apps/web/package.json` (/version); explicit local bump supported. Node package version; independent unless explicitly coupled by the application package below.
- `node-packages-ts-api-client`: `0.1.0` from `packages/ts/api-client/package.json` (/version); explicit local bump supported. Node package version; independent unless explicitly coupled by the application package below.
- `node-packages-ts-contracts`: `0.1.0` from `packages/ts/contracts/package.json` (/version); explicit local bump supported. Node package version; independent unless explicitly coupled by the application package below.
- `node-packages-ts-design-tokens`: `0.1.0` from `packages/ts/design-tokens/package.json` (/version); explicit local bump supported. Node package version; independent unless explicitly coupled by the application package below.
- `node-packages-ts-ui`: `0.1.0` from `packages/ts/ui/package.json` (/version); explicit local bump supported. Node package version; independent unless explicitly coupled by the application package below.
- `python-apps-api`: `0.1.0` from `apps/api/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-apps-worker`: `0.1.0` from `apps/worker/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-packages-py-ai`: `0.1.0` from `packages/py/ai/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-packages-py-data`: `0.1.0` from `packages/py/data/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-packages-py-domain`: `0.1.0` from `packages/py/domain/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-packages-py-ingestion`: `0.1.0` from `packages/py/ingestion/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-packages-py-rules`: `0.1.0` from `packages/py/rules/pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.
- `python-root`: `0.1.0` from `pyproject.toml` (project.version); check-only; native release process owns updates. Independent Python distribution; native package tooling owns uv.lock and workspace constraints.

The machine-readable inventory is `versioning.json`. Cargo workspace inheritance and local lock entries are bound explicitly. Node lock root fields are distinct from registry dependency versions. Tauri versions are bound to their own package; different frontend numbers remain independent.

## Check and change

Python 3.11 or newer; standard library only. Run from the repository root.

```sh
python3 scripts/version_manager.py show
python3 scripts/version_manager.py check
python3 scripts/test_versioning.py -v
```

Choose a single unit. Preview is non-mutating; apply is explicit and should always include the observed current value:

```sh
python3 scripts/version_manager.py bump patch --unit rust-workspace
python3 scripts/version_manager.py bump patch --unit rust-workspace --expect 0.1.0 --apply
python3 scripts/version_manager.py check
```

`set NEW --unit NAME` also previews; add `--expect CURRENT --apply` only after review. For prereleases use `bump prerelease`; stable promotion requires explicit `--allow-stable`. The helper does not run Git, tags, package managers, release hooks, signing, installers or network requests. All source edits are validated before any write.

Check-only units intentionally refuse bump/set because native dependency resolution, existing tooling or historical/test-only ownership must be respected. Incoming local Cargo version requirements and Python workspace locks are not approximated by search-and-replace. Use the project’s native packaging process, update constraints/locks together, then rerun these checks.

## Release history and channels

- No release entry was returned in the repository release inventory reviewed on 2026-10-12. Absence here does not prove the project was never distributed.

This change creates no tag, release, publication, deployment or release date. Record future changes under Unreleased first; use the existing release process and tag namespace only after platform validation and explicit release approval.

## Scope and validation

The added CI checks metadata consistency and dependency-free version-manager safety only, with `contents: read`. Apply/idempotency/stale-source/mismatch checks use temporary fixtures. They do not establish that a native app builds, signs, installs or works on macOS/Windows/Linux. No native packaging or device acceptance was run for this adoption.

Generated Apple projects, vendored/upstream material, historical evidence, protocol/schema/database numbers and dependency versions remain outside product bumps. Existing runtime version mechanisms are retained; there is no UI redesign.

## Adoption base

Source snapshot: [chore/next-security-2026-10-11 @ 58d28fe481df](https://github.com/a1112/HardAtlas/commit/58d28fe481dfa65b5d1d4b13dd65327aedf75b69). Default branch `main` and all existing repair/tag refs are retained.

