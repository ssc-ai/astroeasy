# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
