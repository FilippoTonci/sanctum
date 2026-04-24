# Changelog

All notable changes to the Sanctum Python backend are documented in this
file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project follows semantic versioning once a stable release is cut (currently
pre-1.0: breaking changes can land in any `0.x.y` bump, but every PR still
records its API impact here).

This changelog covers **the Python backend** in this repository
(`sanctum`). The Phase 3 Electron desktop app (`sanctum-desktop`) maintains
its own changelog in its own repository. Each `sanctum-desktop` release
pins a specific `sanctum` commit — see `plans/phase-3-desktop-ui.md` for
the atomic-installer contract.

### Guidance for contributors

Every pull request that changes the HTTP API, the CLI, the on-disk
session/mapping-store format, or any other externally-visible contract
MUST update this file under `## [Unreleased]` with an entry in the
appropriate category. Internal refactors and test-only changes can omit.

Categories (from Keep a Changelog):

- **Added** — new features, new endpoints, new fields, new CLI subcommands.
- **Changed** — non-breaking behaviour changes to existing features.
- **Deprecated** — features still present but planned for removal.
- **Removed** — features taken out this release.
- **Fixed** — bug fixes visible to callers.
- **Security** — vulnerability fixes or hardening measures.

The contract-compat check (Phase 3 WS1 substep 6) refuses PRs that remove
endpoints, remove request fields, add required request fields, or narrow
response fields without a corresponding `## [Unreleased]` entry explaining
the break.

---

## [Unreleased]

### Added

- `/health` now returns `sanctum_commit`: the build-time commit SHA of
  the bundled backend (or `"dev"` when unset). The Phase 3 desktop app
  compares this against the SHA it was built with to detect corrupt
  installs or manually-swapped sidecars. Source: `SANCTUM_COMMIT`
  environment variable, consumed by `sanctum._build_info.commit()`.
  (Phase 3 WS1 substep 1.)
- `CHANGELOG.md` itself — this file. (Phase 3 WS1 substep 1.)

---

## [0.1.0] — unreleased

Initial pre-release. Covers Phase 0 (foundation), Phase 1 (document
processing + API), and Phase 1.5 (review workflow). See `plans/` for
the phase-by-phase breakdown.
