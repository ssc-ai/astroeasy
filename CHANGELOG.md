# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.3] - 2026-08-14

### Added

- **Configurable plate-solve search radius** — `AstrometryConfig.search_radius`
  (degrees, default `10.0`). When the image metadata carries a boresight,
  `solve_field` passes `--ra`/`--dec` with a radius that was previously
  hard-coded at 10 degrees. That is generous for a sensor whose pointing is
  known to better than a degree: the solver spends its `cpulimit` searching sky
  the frame cannot contain, and a frame that will not solve burns the whole
  budget before failing. The default preserves the previous behaviour exactly.
- **Configurable solve acceptance threshold** — `AstrometryConfig.odds_to_solve`
  (default `None`). When set, `--odds-to-solve` is passed to `solve-field`;
  `None` omits the flag and keeps solve-field's own default. Raising it rejects
  marginal solutions that a downstream refinement would otherwise have to detect
  and discard.

### Fixed

- **`solve_field_aggressive` silently dropped six configuration fields.** It
  varies `max_sources` across attempts and rebuilt `AstrometryConfig` by hand to
  do so, copying 7 of the dataclass's 13 fields; `indices_series`,
  `tweak_order`, `output_dir`, `release_index_page_cache`, `search_radius` and
  `odds_to_solve` were reset to their defaults on every attempt. Two of those
  change solving outright — a caller selecting the `5200` index series got
  `5200_LITE`, and one setting `tweak_order=1` got `2` — so the aggressive path
  could fail, or succeed differently, where a direct `solve_field` call with the
  same config would not, and nothing surfaced the substitution. It now uses
  `dataclasses.replace`, which varies the one field by construction and cannot
  go stale as fields are added. **Behaviour change:** callers of
  `solve_field_aggressive` that set any of those six now get their configured
  value, so results on that path may move.

## [1.2.2] - 2026-08-04

Release and development infrastructure only — no library code changed. Note
that 1.2.1 was tagged but never published to PyPI, so this release is the first
to ship the index page-cache fix described below.

### Fixed

- **Broken badge images on the PyPI project page.** The Tests and Coverage
  badges used repo-relative paths, which GitHub resolves but PyPI cannot: PyPI
  renders the README standalone, so both 404'd. They now use absolute URLs.
- **`make install-dev` installed only the `[dev]` extra**, omitting `[cascade]`.
  Without `scipy`/`pillow` four cascade tests failed rather than skipping, so a
  fresh checkout looked broken. It now installs `[catalog,cascade,dev]`.

### Added

- **Continuous integration** (`.github/workflows/ci.yml`) — lint, tests on
  Python 3.11 and 3.12, and a build check on every push and pull request to
  `main`/`dev`. The repository previously had no CI.
- **Automated PyPI publishing** (`.github/workflows/python-publish.yml`) via
  Trusted Publishing (OIDC), so no API token is stored. It triggers only when a
  GitHub Release is published — never on a merge, and never for draft or
  prerelease releases — and pauses for manual approval on the `pypi`
  environment before uploading. Before releasing, it verifies the release tag
  matches `pyproject.toml`, that the version is not already on PyPI, and that
  lint, the test suite, and `twine check` all pass. Setup and procedure are
  documented in `docs/RELEASING.md`.
- **`ASTROEASY_TEST_INDICES`** to point the test suite at astrometry.net index
  files outside the default `/stars/data/share/5000/5200-LITE`. `conftest.py`
  previously claimed this was configurable but hardcoded the path.
- **Contributor documentation** for index files, the automatic container mount,
  the Docker/local test split, and the `ASTROEASY_TETRA3_TESTS` opt-in.

### Changed

- Vendored tetra3 redistribution compliance was audited and recorded in
  `astroeasy/_vendor/README.md`. Apache-2.0 §4(a)–(d) are satisfied and the
  upstream attribution notices are retained; no packaging change was needed.

## [1.2.1] - 2026-07-27

### Fixed

- **Index page-cache growth during repeated solves.** `solve-field` memory-maps the
  index tiles it reads, and the kernel keeps those pages resident in the OS page cache
  after the subprocess exits. A long-lived process that solves many frames sweeping the
  sky accumulated resident index pages without bound (toward the full index size),
  presenting as a slow memory leak. After each solve the index files are now advised
  `POSIX_FADV_DONTNEED`, dropping the clean cached pages; the next solve re-faults only
  the tiles it needs. Only clean pages are dropped, so the behavior is lossless, and it is
  a no-op on platforms without `posix_fadvise` (e.g. macOS, Windows). Enabled by default;
  opt out per-config with `AstrometryConfig.release_index_page_cache = False`.

## [1.2.0] - 2026-06-18

### Added

- **Cascade fast-solver (`astroeasy.cascade`)** — an optional, catalog-native
  escalation solver for fixed cameras that solve many frames. It runs
  cheapest-first — T0 (refine from a prior/boresight) → T1 (tetra3 lost-in-space
  pattern match) → T3/T4 (the existing astrometry.net `solve_field` backstop) —
  and only returns a solution that clears a likelihood-based acceptance gate, so
  a confident-but-wrong match is rejected rather than returned. Entry point:
  `astroeasy.cascade.solve()`.
- **Offline Gaia mirror reader (`astroeasy.catalog.mirror`)** — query a local
  HEALPix-tiled binary star catalog with no network (`query_mirror_box`,
  `query_gaia_field_local`, `read_tile`).
- **New CLI commands** for the cascade workflow: `characterize` (measure a
  sensor from blind-solved frames and persist a profile + artifacts),
  `build-tetra3-db`, and `build-index`.
- **`[cascade]` install extra** (`pip install "astroeasy[cascade]"`) pulling
  `scipy` and `pillow` for the vendored tetra3 pattern matcher.
- `query_gaia_field()` gains an optional `mirror_dir=` argument to serve a query
  from a local mirror instead of the online TAP service.
- Vendored [tetra3](https://github.com/smroid/cedar-solve) (cedar-solve fork,
  Apache-2.0) under `astroeasy/_vendor/` so `[cascade]` is self-contained;
  provenance in `astroeasy/_vendor/README.md`.

### Fixed

- Removed an intermittently failing plotting test (`test_contrast_parameter`)
  that compared two stretches of unseeded random noise; it is now deterministic.
- Mirror tile reads now raise a clear error for a missing tile and warn on a
  truncated/non-record-aligned tile, instead of failing opaquely or silently
  dropping records.
- Sensor characterization no longer emits a (degenerate) rotation prior from
  fewer than three frames, and surfaces a tied parity vote instead of silently
  defaulting it.

### Backwards compatibility

**This release is fully backwards compatible.** The cascade is purely additive:
`astroeasy.__init__`, `solve_field`, the models, config, indices, and Docker/
local backends are unchanged, and the core dependency set is unchanged. Users
who use astroeasy for astrometry.net plate solving need nothing new — the
cascade and its heavy dependencies live behind the optional `[cascade]` extra
and the `astroeasy.cascade` namespace, imported lazily, so importing and using
the library without that extra installed works exactly as in 1.1.0.

## [1.1.0] - 2026-06-04

### Added

- FOV-filtered index downloads (`--fov`) to fetch only the index files needed
  for a camera's field of view, and a matching `examine --fov` validation.
