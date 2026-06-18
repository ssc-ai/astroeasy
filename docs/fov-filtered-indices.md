# FOV-filtered indices: senpai integration + runtime index selection

Status notes for the FOV-filtered index feature (`download_indices(..., fov_degrees=)`,
`examine_indices(..., fov_degrees=)`, CLI `--fov`). Verified 2026-06-04 end-to-end:
`indices download --series 4200 --fov 2.0` fetched exactly scales 5–11 (40 of 288
files, 1.3GB of 31.5GB), and a real DAO01 8120px frame solved against just that
subset through senpai `cli.rate`.

## 1. senpai integration (deferred until this feature is released)

senpai's `enforce_indices()` / `examine_indices()` (`senpai/astrometry.py`) call
`astroeasy.examine_indices()` without `fov_degrees`, so a FOV-filtered partial set
only passes validation via `indices_series: CUSTOM` (which skips validation
entirely). Plan:

- Add an optional FOV field to senpai's `AstrometryConfig` (or derive it — see §2)
  and pass it through to `astroeasy.examine_indices(..., fov_degrees=...)`.
- senpai pins `astroeasy>=1.0.14`; needs a release containing the fov code.

## 2. Fancy: runtime index selection when a FULL set is on disk

If a FOV is known but the indices directory holds a *full* series, the generated
`astrometry.cfg` should list only the index files needed for that FOV, instead of
loading everything — for solve speed and memory.

Today `_write_astrometry_cfg()` (`astroeasy/dotnet/runner.py`) writes:

```
inparallel
add_path {indices_path}
autoindex
```

`autoindex` + `inparallel` makes solve-field open every index in the directory —
all 288 files / 31.5GB for a full 4200 series, when a 2° field needs 40 files /
1.3GB.

Proposed: when a FOV is available, replace `autoindex` with explicit lines for the
filtered subset (only files that actually exist on disk):

```
inparallel
add_path {indices_path}
index index-4205-00.fits
index index-4205-01.fits
...
```

Building blocks already exist: `scales_for_fov()` + `filter_structure_by_scales()`
(`astroeasy/indices.py`) give the filename set; intersect with `os.listdir` of the
indices dir so a partial download and a full set both work.

Key observation — **no new config may be needed**: `AstrometryConfig` already
carries `min_width_degrees` / `max_width_degrees` (passed to solve-field as
`--scale-low/--scale-high`). The needed quad range is derivable from those bounds:
quads from `0.1 * min_width * 60` to `max_width * 60` arcmin, i.e. the union of
`scales_for_fov()` across `[min_width, max_width]`. So astroeasy could filter the
cfg automatically whenever scale bounds are set, and senpai gets the speedup for
free — its DAO config already sets 1.0–3.0°.

Caveats:
- A per-solve FOV hint (e.g. from `ImageMetadata`) should win over the config-wide
  scale bounds when present.
- `SERIES_CUSTOM` / unrecognized filenames must fall back to `autoindex`
  (filename-pattern parsing only knows `index-NNSS[-HH].fits`).
- Keep `autoindex` as the default when no scale information is available.
