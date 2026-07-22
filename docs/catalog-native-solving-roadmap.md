# Catalog-native plate solving: escalation cascade, mirror API, and a Rust core

**Status:** **Python implementation landed** (2026-06-11, `add-fast-solving` branches
in both repos): WS-A (catalog primitive), WS-C (`astroeasy.cascade`: T0/T1/gate/
`solve()`), WS-I (`astroeasy build-tetra3-db`), WS-G (`astroeasy characterize`),
WS-B v0 (`astroeasy build-index`), WS-F (senpai `solver_mode` wired). Validated on
real DAO frames: 12/12 sidereal via T0 and T1 (0.35–2.2″ vs ground truth,
~170–270 ms warm); rate-track 5/6 at T0 bright-cone with the miss REJECTing cleanly.
Direction originally **validated by benchmark** (2026-06-10) — see
[`../benchmarks/RESULTS.md`](../benchmarks/RESULTS.md) and `benchmarks/fast_solve/`.
Remaining: WS-J beyond geometric-v0 (photometry/colour terms), WS-E (mirror API),
WS-H (chop), shadow-mode rollout (§9), Rust port (§6).
Extends [`fov-filtered-indices.md`](./fov-filtered-indices.md).
**Scope:** spans `astroeasy` (this repo, incl. `benchmarks/fast_solve/`), `senpai`
(`/stars/src/senpai`), the local Gaia mirror (`/media/.../gaia_g21`), and a proposed
Rust core + HTTP service.
**Authors:** zgazak + Claude (design + benchmark 2026-06-10).

---

## TL;DR

Stop treating the 35 GB astrometry.net index as the primitive. The **80 GB local
Gaia mirror (G≤21, 1.74 B stars)** is strictly more general: from it you can (a)
answer catalog cone queries, (b) build any astrometry.net index on demand, and (c)
feed a direct *project-and-match* solver that needs **no index at all**.

Build a single `solve()` entry point that runs an **escalation cascade**,
cheapest-first, returning on the first verified solve and falling through only on
failure:

```
T0 Refine    a prior (prev WCS / boresight)  <100ms  catalog,     native (constrained.py)
T1 Match     scale only, no position         ~2ms    pattern DB,  tetra3   ✓ 97% sidereal
T2 Fast-deep deep/dense tail                  0.1–2s  deep index,  a.net / native quad
T3 Hinted    boresight + scale               ~4s      stock index, astrometry.net
T4 Blind     nothing                         ~5s+     stock index, astrometry.net
```

- **T0/T1 are native and live everywhere** (incl. the edge), need only the catalog.
- **T2–T4 are astrometry.net** and live **only on a server**, behind a mirror-API
  `/blind` endpoint. The edge never installs Docker/astrometry.net.
- Always try T0/T1 first (trivially cheap), escalate only on a failed **gate** (§4).
  Real-time on the happy path; never worse than astrometry.net on the worst case.
- **Measured on DAO (§3.3):** T0/T1 carry sidereal cold-starts + any prior'd frame on
  the ms path; the residual is the **deep/dense tail** (rate-track cold-start), which
  needs a *deep* solver (T2 native quad-matcher, or astrometry.net T3) — the boresight
  buys **speed, not robustness**.

The strategic bet: **do our absolute best to never call astrometry.net** — it becomes a
rarely-touched deep safety net and a one-time **sensor-characterization** tool.

---

## 0. Compatibility stance (hard constraints on *how* this lands)

Added 2026-06-10. Two product constraints govern every workstream below.

### 0.1 astroeasy stays "astrometry.net made easy"

astroeasy's identity — *wrap solve-field with a clean Python API and easy Docker
install* — is preserved for users who want exactly that and nothing more:

- The existing public API (`solve_field()`, `AstrometryConfig`, the
  `Detection`/`SolveResult` models, index download/verify, the Docker image) keeps
  its behavior and signatures. A pure astrometry.net user sees zero change.
- The cascade lands as a **new, additive subpackage** — `astroeasy.cascade` — with
  its own entry point (`solve()`). Catalog-native pieces live in
  `astroeasy.catalog` (the local-mirror reader joins the existing online query).
- New/heavy dependencies are **optional extras** (`pip install astroeasy[cascade]`)
  with lazy imports; the base install's dependency set is unchanged. **Decided
  (2026-06-11):** cedar-solve's PyPI release pins `numpy<2`/`Pillow<9` (stale —
  the code runs fine on numpy 2.x, proven on DAO), so its four core modules are
  **vendored** at `astroeasy/_vendor/tetra3/` (Apache-2.0, provenance in the
  README there) and `[cascade]` pulls just `scipy` + `pillow`. A plain
  `pip install astroeasy[cascade]` is fully self-contained.
- README/docs framing: astrometry.net wrapper first; the cascade is an optional
  advanced layer documented separately.

### 0.2 senpai: additive solver modes, zero config breakage

Existing senpai deployments carry YAML configs whose `astrometry:` block must keep
working unchanged. The cascade is an **opt-in mode**, not a replacement:

- New field `astrometry.solver_mode`, **default `"dotnet"`**. Absent from every
  existing config → all current deployments parse and behave identically.
- Modes:
  - **`dotnet`** (default) — today's path: unconditional astroeasy `solve_field()`.
  - **`tetra3`** — native-only (T0 refine + T1 tetra3) from catalog + pattern DB;
    no Docker/astrometry.net needed; fails fast on the deep tail. For edge boxes
    and experimentation.
  - **`chain`** — the full cascade (T0 → T1 → [T2] → T3/T4) with astrometry.net as
    backstop; requires a valid dotnet config plus the fast-solve artifacts.
- Cascade-specific settings (mirror dir, tetra3 DB ref, gate thresholds /
  sensor-profile ref) live in a new optional `astrometry.fast_solve:` sub-block,
  fully defaulted; `dotnet` mode never reads it.
- Mode dispatch lives inside the adapter `senpai/astrometry.py`; the call sites
  (`photometry_pipeline.py`, `collect.py`, `sidereal.py`, `rate.py`,
  `api/routes/astrometry.py`) keep calling `solve_field(sources, wcs)` unchanged.

### 0.3 T2 (native deep quad-matcher): explicitly deferred

The rate-track cold-start tail (§3.3) keeps astrometry.net (T3) as its solver.
Build the native constrained deep quad-matcher only when production measurement
shows (a) rate-track cold-starts (no usable prior) are frequent, and (b) ~4 s of
T3 on those frames breaks a real latency budget. The cascade keeps the T2 slot;
until then `chain` = T0 → T1 → T3 → T4.

---

## 1. Why (the reframe)

An astrometry.net index is a precomputed answer to the *blind, no-prior, anywhere*
problem. But senpai rarely has that problem. At solve time it holds:

| Signal | Source (today) |
|---|---|
| Boresight RA/Dec | `extract_boresight_from_header` (`senpai/engine/utils/fits_io.py`) |
| Plate scale (iFoV) | fixed sensor characteristic → `min/max_width_degrees` |
| Previous-frame WCS | `propagate_wcs.py` + refinement in `wcs_refinement.py` |
| Sub-second cone query | `gaia_local.query_by_ra_dec_bounds` (local mirror) |

Yet `senpai/engine/processing/photometry_pipeline.py:71` calls
`solve_field(sources)` with **no WCS prior**, so every frame shells out to Docker →
astrometry.net → 35 GB index. The only constraint astroeasy applies is a
**hardcoded `--radius 10.0`** (`astroeasy/dotnet/runner.py:430`) plus scale bounds.
senpai is paying for a constrained-blind solve on every frame while holding
everything needed to just project-and-match.

The catalog, not the index, is the right thing to standardize on. Indices become a
*derived, disposable* artifact — or are skipped entirely (T0/T1).

### Consolidation bonus

The 80 GB Gaia can **replace** rather than add to current data:

- **35 GB stock indices** → built on demand from the catalog (or skipped at T0/T1).
- **17 GB sstr7c** → Gaia G≤21 is deeper, and the mirror already synthesizes
  `Johnson_V`/`Sloan_r` from BP−RP (`senpai/catalog/gaia_transforms.py`). One
  catalog serves photometry, identification, *and* solving.
- **`gaia_g21/chunks/` (72 GB)** is the raw `random_index` download, redundant once
  `mirror/` is ingested. **Reclaimable now** → ~72 GB back (143 GB → ~72 GB).
- **Depth-chopping** the mirror to a sensor's real limiting magnitude shrinks the
  *full local catalog* dramatically (all-sky G-cumulative, ~44 B/star):
  G≤21 ≈ 1.74 B / 72 GB → G≤18 ≈ 300 M / ~13 GB → G≤16 ≈ 70 M / ~3 GB →
  G≤14 ≈ 13 M / sub-GB. Same `MIRROR_DTYPE`, so every consumer works unchanged → a
  sensor good only to G18 can carry a *full but smaller* local catalog (WS-H).

Framing: *one 80 GB catalog replacing 35 + 17 GB + per-series index sprawl*, not
"+80 GB" — and chop-to-depth for any given sensor.

---

## 2. Current state (grounded inventory)

What already exists, so the plan reuses rather than rebuilds.

### astroeasy (this repo)
- `solve_field(detections, metadata, config, existing_wcs)` — the astrometry.net
  wrapper; `existing_wcs` path already supports verify/refine (`runner.py:23`).
- `solve_field` **already adds `--ra/--dec/--radius 10.0`** from
  `metadata.boresight_ra/dec` when present (`dotnet/runner.py:425–431`); scale via
  `--scale-low/--scale-high` from config (`:434–435`).
- `scales_for_fov(fov_degrees)` + `filter_structure_by_scales()` (`indices.py`) —
  FoV → needed scale numbers. `INDEX_SCALE_RANGES` (`constants.py`) is the scale
  table.
- `AstrometryIndexSeries.SERIES_CUSTOM` already exists; validation is stubbed to
  skip it (`indices.py:245`).
- `catalog/gaia.py: query_gaia_field` — **online** astroquery/TAP query (not local).
- FoV-filtered download verified end-to-end (`docs/fov-filtered-indices.md`).
- **No index-building tooling exists** (`build-astrometry-index` ungrepped anywhere).

### senpai
- `senpai/astrometry.py` — thin adapter delegating to `astroeasy.solve_field`.
  Callers (2026-06: five, not one): `photometry_pipeline.py:71`, `collect.py`,
  `sidereal.py`, `rate.py`, `api/routes/astrometry.py` — adapter-level dispatch
  (§0.2) covers all of them at once. The photometry path passes **no prior**.
- `catalog/gaia_mirror.py` — builds the trimmed local mirror (download + ingest).
- `catalog/gaia_local.py` — `query_by_ra_dec_bounds(..., mirror_dir=)`, drop-in for
  the online query, **sub-second per field**, reads only overlapping tiles.
- `engine/utils/wcs_refinement.py` — full **project → match → fit TAN+SIP**
  refinement (sidereal + rate-track). This *is* T0, already built.
- `engine/utils/propagate_wcs.py`, `wcs_ops.py`, `wcs_helpers.py` — shift/propagate
  WCS, match stars to detections, MAD outlier rejection, SIP refit. T0/T1 plumbing.

### Local Gaia mirror (`<path-to>/gaia_g21/mirror/`)
- **1,736,684,054 stars**, G≤21, 72 GB, **3072 HEALPix level-4 tiles** (~3.7°),
  bbox `index.json`, fixed-width records:
  ```
  MIRROR_DTYPE = i8 source_id, f8 ra, f8 dec, f4 g, f4 bp, f4 rp, f4 pmra, f4 pmdec
  ```
- Tiles derived **for free** from `source_id` bits (`hpx = source_id >> 51`); no
  healpy. Stars/tile: 30 k (poles) … 16.7 M (galactic plane), mean ~565 k.
- This format is **already mmap-able and Rust-friendly** — the key enabler for §6.

---

## 3. The escalation cascade

A single `solve(detections, profile, prior_wcs=None) -> SolveResult` runs the ladder
cheapest-first, returns on the first **accepted** solve, escalates on rejection.

| Tier | Name | Preconditions | Method | Engine | Index | Cost | Lives |
|---|---|---|---|---|---|---|---|
| **T0** | Refine | a prior — prev-frame WCS, or boresight | project cone → match → fit TAN+SIP. Full prior: trivial. Boresight-only: + roll/parity search (`constrained.py`) | native | — (catalog) | <100 ms | edge + server |
| **T1** | Match | scale/FoV known, no position | **tetra3 / cedar-solve** lost-in-space pattern hash → coarse RA/Dec/roll/FoV → SIP polish | tetra3 | pattern DB (from catalog) | ~2 ms | edge + server |
| **T2** | Fast-deep | deep/dense tail (e.g. rate-track cold-start) | deep+specific solver: custom **deep** index (a.net engine, small) or a native **quad** matcher — §3.3 | a.net / native | custom deep | ~0.1–2 s | server (fat edge) |
| **T3** | Hinted | scale bounds only | astrometry.net on **stock** all-sky index + scale hints | a.net | stock 35 GB | ~1–10 s | server |
| **T4** | Blind | nothing | astrometry.net stock index, no hints | a.net | stock 35 GB | ~s–min | server |

Mapping to your phrasing: T0 is the prior-refine; "always going T1 T2 T3 T4 until
success" is the escalation through the rest. T2/T3/T4 are the **same engine**
(astroeasy's `solve_field`) with different (index, hint) settings → one
implementation, parameterized.

### Design principles

1. **Cheapest-first, escalate on failure.** No upfront routing decision; the cost of
   attempting T0/T1 is negligible vs a wrong route.
2. **Every tier must reliably self-assess failure** (§4). The cascade's correctness
   hinges on never *accepting a wrong solve* and thereby skipping the fallback that
   would have succeeded — your hard constraint ("never fail a frame astrometry.net
   would have gotten").
3. **astrometry.net only at T2–T4, only server-side.** Edge = T0/T1 native +
   (optional) a local custom index for T2; else escalate to the API.
4. **Short-circuit by capability.** If no prior → skip T0. If no catalog locally →
   T0/T1 require `/cone` or escalate to `/solve`. If `profile.scale` unknown → only
   T3/T4 are valid (this is the characterization path).

### 3.1 FoV ↔ depth ↔ engine (why one tier doesn't fit all sensors)

Pattern/quad matching needs ~10–30 stars *in the frame*. Star count =
sky-density(at the limiting magnitude) × FoV area, and density climbs steeply with
depth. All-sky averages (Gaia G; galactic plane 10–100× denser, poles sparser; the
G<21 row ties out to the mirror's 1.736 B):

| Limit | ~stars/deg² | in 2×2° (4 deg²) | in 0.3×0.3° (0.09 deg²) |
|---|---|---|---|
| G<10 | ~8 | ~33 | **~0.7** |
| G<12 | ~56 | ~220 | ~5 |
| G<14 | ~315 | ~1,300 | ~28 |
| G<16 | ~1,700 | ~6,800 | ~150 |
| G<18 | ~7,300 | ~29,000 | ~660 |
| G<21 | ~42,000 | ~170,000 | ~3,800 |

Shrink the FoV 44× (2° → 0.3°) and you must go **~4–6 magnitudes deeper** to keep
the same star count. A 0.3° field is essentially *empty* at the bright magnitudes
tetra3 lives on (<1 star at G<10) and only becomes solvable around G14–16 (sky-avg;
worse at high latitude). Hence **engine choice is FoV-driven**:

- **Wide FoV (≳1–2°):** a handful of bright stars suffice → **tetra3, shallow DB**,
  tiny and fast. T1 carries most cold-starts.
- **Narrow FoV (≲0.5°):** needs faint stars → tetra3's bright-sparse assumption
  breaks (§3.2) → **deep custom index (T2) or astrometry.net (T3)**. *But* with a
  prior even a narrow sensor mostly does **T0** on a deep catalog cone (a few hundred
  stars, no index) — so the genuinely hard residual is **narrow FoV + no prior**.

**"Isn't that why astrometry.net indices are so big?"** — partly. Index size =
*depth-at-scale* × *sky coverage* × *number of scales*. Two effects get conflated:
1. **Depth:** small-FoV (high scale-number) indices hold fainter stars to have enough
   per field → more stars → bigger. The deep small-field scales (index-4200/4201,
   ~250–440 MB × 48 tiles) really are the bulk of the 35 GB. ✅
2. **Scale coverage (why the *full set* is 35 GB):** stock 4200/5200 span every FoV
   from ~2′ to ~34° all-sky; any one camera uses 2–4 of ~20 scales. Most of the 35 GB
   is scales you'll never load.

A custom index for a 0.3° sensor therefore drops the *scale* bloat but **keeps the
depth** — deep-but-single-scale, built from local Gaia to *exactly* the sensor's
limiting magnitude (not astrometry.net's one-size-fits-all depth). Not tiny, but a
fraction of 35 GB and matched to the sensor.

### 3.2 Prior art: tetra3 / cedar-solve

[esa/tetra3](https://github.com/esa/tetra3) (and the more active fork
[smroid/cedar-solve](https://github.com/smroid/cedar-solve)) is a proven, Apache-2.0
lost-in-space pattern-hash solver: build a per-FoV pattern DB from a star catalog,
centroid, hash N-star patterns, look up, verify — ~10 ms, no prior needed. It *is*
T1, better-built than anything we'd hand-roll → **adopt it for T1**, don't reinvent.

Boundaries that keep it to one rung:
- **Shallow by construction** — default DB is mag 7 (Yale BSC5); the deepest shipped
  catalog (Tycho) is mag 10, called "sufficient for all tetra3 databases." Bright-star
  pattern hashing relies on a sparse, *reproducible brightest-N* selection; going deep
  explodes the pattern catalog + false-match rate and breaks brightest-N
  reproducibility near the completeness limit → fails narrow-FoV/deep/crowded (§3.1).
- **No prior** — ignores the boresight/prev-frame prior senpai usually has; that's
  what **T0** exploits, and T0 is both cheaper and deeper-capable than tetra3.
- **Distortion = one scalar, not SIP** (verified): `solve_from_centroids(distortion=)`
  takes a known scalar or a `(min,max)` search range and returns a scalar radial term
  ("amount at width/2 from centre; negative barrel, positive pincushion"); output is
  RA/Dec/roll/FoV + that scalar, **no SIP coefficients**. For **DAO this is the
  favourable case** — pincushion is radial, exactly the model's shape — so a
  profile-measured distortion (seeded from `/blind`, §5) lets tetra3 *match through*
  the pincushion. It does **not** give precise astrometry: senpai still fits full
  **SIP (order 2)** in T0/refine after the lock. Division of labour: tetra3 scalar →
  get the match; senpai SIP → get the accuracy.

Net: tetra3 = the T1 engine; T0 sits above it, astrometry.net (T3/T4) below it for
the depth its ceiling can't reach. Its DB is just another small derived artifact
built from the 80 GB Gaia (WS-I) — a sibling of the custom index, not a replacement
for the catalog.

**Measured on DAO (2026-06-10 — `benchmarks/RESULTS.md`).** The gating question is
answered: tetra3-as-T1 works. On 97 real DAO frames vs senpai's ground-truth WCS,
with a **323 MB** DB built from the **local Gaia mirror** (G≤11, 2.1°, no download):
- **sidereal (point-source) frames: 97% lock @ ~1.6 ms**, 2″ center — astrometry.net's
  fit rate at ~3000× the speed (astrometry.net here: ~100% @ ~4–6 s, 0.26″).
- rate-track (streaked) frames: 78%; relaxing tetra3 doesn't fix them (structural —
  tracked object + wobbly faint stars in the brightest-N) → those go **T0** (they have
  a prior), exactly as the cascade intends.
- DAO **pincushion was a non-issue** (`distortion=None`). Gotchas: senpai detection
  `y` is FITS-from-bottom → flip for tetra3; Gaia→tetra3 catalog needs a 3-int ID field.

Verdict: adopt tetra3/cedar-solve for T1 (WS-D), build its DB from local Gaia (WS-I).

### 3.3 What the DAO benchmark taught us (and how it sharpens the ladder)

Full results in [`../benchmarks/RESULTS.md`](../benchmarks/RESULTS.md); harness in
`benchmarks/fast_solve/`. On 97 real DAO frames (8120², 0.918″/px, 2.07° FoV; ground
truth = senpai's own WCS):

- **T1 (tetra3) validated for point-source cold starts:** sidereal **97% @ ~1.6 ms**,
  ~2″ center — astrometry.net's hit rate at ~3000× the speed, from a **323 MB**
  Gaia-built DB (vs 32 GB stock). Pincushion a non-issue.
- **T0 (constrained refine) works for the easy case:** the `constrained.py` prototype
  (project the boresight cone, brute-force roll+parity, fit) locks sidereal/sparse
  frames sub-arcsec to ~3″ in <100 ms; with a *full* prior WCS (roll known) it's
  trivial. Both fast rungs are real.

The correction to the original plan is the **rate-track tail** (tetra3 78%; the ~22% it
misses):

- **It's depth + density, NOT the boresight.** Proven three ways: astrometry.net
  *blind* ≈ *hinted* (94% vs 95%) → the pointing hint isn't carrying it; every
  bright-only matcher fails the *same* frames (tetra3 G≤11 → 78%; a boresight-constrained
  *bright* matcher → 69%); the matchable signal lives in *faint* stars (the bright
  detections are few + the tracked object). astrometry.net wins via its **deep index**,
  not the prior. The boresight buys **speed, not robustness** (hinted ~4 s vs blind p95 35 s).
- **A simple fast matcher can't substitute.** Given the full deep cone (15k–86k stars),
  the constrained *pair-translation* vote returns **noise** — confident-wrong solves
  ~140″ off, because random coincidences are likely in a dense catalog. That is exactly
  why astrometry.net/tetra3 use 4-star **quad hashing** (specific), not pairs.

**Net effect on the ladder:** T0/T1 carry the bulk (any prior'd frame + sidereal
cold-starts) on the ms path. The deep/dense tail (rate-track cold-start) needs a
**deep + specific** solver — today that *is* astrometry.net (**T3**, the right tool, not
a crutch). A **fast** version (**T2**) means a native *constrained deep quad-matcher* —
real work, justified only if ~seconds on that ~22% is unacceptable. Pair-voting and
tetra3-tuning provably don't get you there.

---

## 4. Acceptance gate — a likelihood model (correctness backbone *and* throttle)

The most load-bearing component, and richer than a checklist. Each rung self-verifies
its **own** candidate WCS — never run two solvers to cross-check (that would always pay
the slow path). The gate is a **likelihood ratio**: project the catalog through the
candidate WCS and score `P(detections | this WCS) / P(detections | random)`. It is
**solver-agnostic** — the same function scores T0, tetra3, and astrometry.net — so the
cascade gets uniform confidence and a clean accept/reject at every rung.

**Why a likelihood, not a match count (learned the hard way, §3.3).** Raw count fooled
the prototype: in a dense cone a *wrong* WCS racked up 18–22 "matches" — but that many
coincidences are *expected* at that density. Log-odds normalizes by the chance rate,
scores them ~zero, and **rejects → escalates**. The gate must be matches-*vs-chance*, or
dense-field confident-wrong solves leak through and stop the cascade on a bad answer.

**Evidence terms** — each pushes the odds toward "real" or "coincidence". We already
have all of it (senpai photometry + the Gaia catalog's per-star data), which is *why*
catalog-as-primitive beats a thin index: **the gate can be as rich as the catalog.**
- **Geometric:** positional residual of each matched pair vs the chance rate at the
  field density (the astrometry.net-style Bayesian core) + post-fit RMS + scale/parity/
  SIP sanity (`compute_wcs_distortion_metrics`).
- **Photometric:** fit a zero-point from the candidate matches (senpai's robust ZP),
  then weight each pair by its mag↔flux residual — a true match sits on the mag–flux
  line, a coincidence is photometrically random. *This term alone kills the dense-field
  noise above.*
- **Colour / spectral:** Gaia BP−RP → predicted in-band mag via the sensor bandpass
  (`gaia_transforms` colour terms); an independent check, stronger if multi-band.
- **Negative evidence:** a bright catalog star predicted in-frame with no detection, or
  a bright detection with no catalog star, push the odds *down*.
- **Proper motion / epoch:** project high-PM stars to the observation epoch (`pmra/pmdec`
  in the mirror) for cleaner residuals.

**The gate is also a throttle, not just a safety check.** Richer evidence cuts both
ways: *more specific* (rejects coincidences) **and** *more sensitive* (a true solve clears
the bar with fewer geometric matches because photometry+colour make up the confidence).
Sensitivity directly shrinks the fraction that falls through to astrometry.net — so
investing in the gate makes the **whole cascade faster on average**, not only safer.

Thresholds live in the **sensor profile** (§5), tuned per sensor at characterization.

> Invariant: a rung that can't clear its own gate **must** REJECT, never a low-odds
> ACCEPT. Test adversarially (wrong priors, sparse *and* dense fields, rotated/parity-
> flipped frames) — the benchmark already surfaced confident-wrong pair-vote solves
> (~140″ off, high match count) as the exact failure to guard against.

---

## 5. Sensor profile + characterization flow

The artifact bridging the one-time blind solve to all subsequent fast solves.

```yaml
# sensor_profile.yaml  (one per sensor; produced by characterization, consumed by T0–T2)
sensor_id: dao01
pixel_scale_arcsec: 0.918         # iFoV (measured on DAO)
fov_degrees: [2.05, 2.08]         # measured field width range (~2.07°)
scale_bounds_degrees: [1.0, 3.0]  # → --scale-low/high and scales_for_fov()
rotation_prior_deg: null          # alt-az + steerable → per-frame field rotation, unknown
distortion: { sip_order: 3, coeffs: ... }   # DAO pincushion; seed for T0 SIP fit
mag_depth_g: 15.8                 # measured limiting G → catalog/index depth
sky_access: all                   # steerable (this tech): all-sky
custom_index: { ref: "g18.5_s1-3", path/url: ... }   # built on demand, cached
gate: { min_matches: 8, max_rms_px: 0.5, min_log_odds: 12 }
```

**Characterization (onboarding / first light):**
1. New sensor, unknown geometry → call **`/blind`** (T4) on a handful of frames.
2. Measure FoV, iFoV (pixel scale), rotation, SIP distortion, limiting magnitude.
3. Persist a `sensor_profile`.
4. **Build the T1/T2 solving artifacts only if absent** (idempotent, keyed by
   `(fov, depth, sky)`): a **tetra3/cedar-solve pattern DB** from the bright slice
   for wide FoV (WS-I), and/or a **deep custom index** via `build_custom_index()` for
   narrow FoV (WS-B). Which one(s) follows from §3.1. Cache server- or edge-side.
5. From then on the sensor runs T0→T1 (→ T2 if a custom index is local), touching
   the server only on fall-through.

This is exactly your "`/blind` to measure things the system needs for T0–T3, then
`build_custom_index()` only if absent."

---

## 6. The Rust core (scoped)

A single embeddable engine shared by senpai / allclear / astroeasy / the API.

**In scope**
- **Catalog reader:** mmap the mirror tiles (reuse `MIRROR_DTYPE` **verbatim** —
  zero new data engineering), `hpx = source_id >> 51`, bbox `index.json` → tile
  selection, cone/box query.
- **T0 Refine:** project cone via a TAN+SIP WCS, nearest-neighbour match, weighted
  least-squares WCS fit.
- **T1 Match:** *adopt tetra3/cedar-solve* — validated (§3.3), don't hand-roll. A
  native deep matcher is the **T2** option for the deep/dense tail, and it must be
  **quad-hashing, not pair-voting** (pair-voting provably returns dense-field noise,
  §3.3) — build only if that tail must be fast.
- **Acceptance gate** (§4) — the likelihood model (geometry + photometry + colour),
  shared/solver-agnostic; the correctness backbone *and* the cascade throttle.
- **PyO3 bindings** (maturin) so senpai calls a function, not a subprocess.
- Thin **CLI** + optional **axum** server (§7) over the same core.

**Out of scope (deliberately)**
- A from-scratch *all-sky blind* solver. T2–T4 stay astrometry.net. Battle-tested,
  rarely hit, not worth reimplementing — revisit only if T1 cold-starts prove
  frequent enough to justify a native blind.

**Why Rust here specifically** (not generic rewrite-itis): the hot path is
geometry/search (cone scan, hashing, RANSAC, WCS fit); the catalog is already a flat
fixed-width binary (mmap + `bytemuck`, trivial); you want one **static binary + the
catalog** to deploy to an edge sensor with **no Docker, no 35 GB**. The existing
mirror format is the low-friction enabler.

**Build-vs-prove (started):** `benchmarks/fast_solve/` already prototypes **T1**
(tetra3/cedar-solve) and **T0** (`constrained.py`: cone + roll/parity + fit) in Python
and validates them on DAO (§3.3). Next: port the **T0 catalog-cone + refine** hot path
to Rust; T1 stays tetra3; a native deep **quad** matcher only if the §3.3 tail must be fast.

---

## 7. Mirror API service

One service over the catalog. The **only** place astrometry.net + stock indices live.
**Runs local or remote** — in practice it's often spun up *locally* (co-located on
the sensor box or a LAN host) over a **chopped** catalog (WS-H), not a far server. So
"server tiers" below means "the API process," wherever it runs; slimming the 80 GB
(depth-chop) directly shrinks that local footprint.

| Endpoint | Body / params | Returns | Purpose |
|---|---|---|---|
| `GET /cone` | `ra, dec, radius, depth, fmt` | catalog slice | = `gaia_local.query` over HTTP; for nano clients & remote refinement |
| `POST /index` | `{fov\|scale_set, depth, sky}` | custom index bundle (tarball/URL), cached | build-on-demand `build_custom_index()` |
| `POST /blind` | `{detections\|image, scale_bounds?}` | WCS + measured FoV/iFoV/distortion | T3/T4; seeds a sensor profile (characterization) |
| `POST /solve` | `{detections, profile}` | full cascade result | runs T0–T4 server-side for nano clients |

Implementation: **axum** over the Rust core if §6 exists; otherwise **FastAPI** to
bootstrap (senpai already runs FastAPI under `senpai/api/`). Index/`/blind` work can
shell to astroeasy regardless.

### Deployment matrix

T0/T1 (native) **require a star catalog** (the cone). Be honest about that:

| Deployment | Local data | Local tiers | Server tiers (via API) |
|---|---|---|---|
| **Fat edge** | 80 GB catalog + Rust engine | T0, T1 | T2–T4 |
| **Medium** | depth/region-trimmed catalog + engine | T0, T1 | T2–T4 |
| **Thin** | custom index only + engine | T2 (a.net-style, *needs local a.net* — usually skip) | T0/T1 via `/cone`; T3/T4 |
| **Nano** | nothing | — | everything via `/solve` |

For fully-steerable sensors (your current reality) the sweet spot is **Fat/Medium**:
chopped catalog + engine, **no local indices at all**, escalating to a (possibly
local) API only for the rare blind. Because the API can be co-located, "Thin/Nano"
may just mean *a local API process over a depth-chopped catalog* rather than a remote
dependency — the chop (WS-H) is what makes a local API host practical.

---

## 8. Workstreams

Each is independently shippable; dependencies noted. Status 2026-06-11:
**landed** = WS-A, WS-B (v0), WS-C, WS-F, WS-G, WS-I, WS-J (geometric v0);
**open** = WS-D (Rust), WS-E (API), WS-H (chop), WS-J (photometry/colour terms).

- **WS-A — Consolidate the catalog primitive (astroeasy).** Move the local-mirror
  reader (`senpai/catalog/gaia_local.py` + `MIRROR_DTYPE`) into
  `astroeasy/catalog/` as the **offline** backend; make `query_gaia_field` dispatch
  local-mirror → online. senpai then imports from astroeasy. *Enables everything.*
- **WS-B — `build_custom_index()` (astroeasy).** mirror cone/tile query → FITS
  table → `build-astrometry-index` per scale (`scales_for_fov`) → `SERIES_CUSTOM`
  bundle + `astrometry.cfg`. Idempotent cache keyed by `(scale_set, depth, sky)`.
  Deps: WS-A. Reuses `SERIES_CUSTOM`, `scales_for_fov`, `filter_structure_by_scales`.
- **WS-C — Cascade solver, Python-first (astroeasy).** `solve(detections, profile,
  prior_wcs)` running T0 (port `wcs_refinement` logic) → T1 (tetra3/cedar-solve) →
  T2/T3/T4 (existing `solve_field`, parameterized by index+hints). The **acceptance
  gate** (WS-J) as a shared function. T0 + T1 are prototyped in `benchmarks/fast_solve/`
  (`constrained.py`, tetra3) — promote those. Lands as `astroeasy.cascade` behind
  the `[cascade]` extra (§0.1); the dotnet API is untouched. Deps: WS-A, WS-B, WS-J.
- **WS-D — T1 engine (tetra3, ✓ validated §3.3) + Rust core for T0/catalog.** tetra3/
  cedar-solve evaluated on DAO (97% sidereal @ 1.6 ms). Port the catalog reader + T0
  cone/refine (prototyped in `constrained.py`) to Rust (PyO3) for the embeddable engine.
  A native **deep quad-matcher** (T2, *not* pair-voting — §3.3) only if the deep/dense
  tail must be fast. Deps: WS-A, WS-C, WS-I.
- **WS-E — Mirror API (astroeasy or new service).** `/cone`, `/index`, `/blind`,
  `/solve`. Deps: WS-A (cone), WS-B (index), existing `solve_field` (blind), WS-C
  (solve).
- **WS-F — senpai integration.** Additive `solver_mode` (`dotnet`/`tetra3`/`chain`,
  default `dotnet` — §0.2) dispatched inside `senpai/astrometry.py`; propagate
  prev-frame WCS frame-to-frame; plumb the sensor profile; optional `fast_solve:`
  config sub-block for tier set + API endpoint. Existing configs unchanged.
  Deps: WS-C (min), WS-A.
- **WS-G — Characterization + profile.** `/blind`-driven measurement → `sensor_profile`
  schema + store; "build artifacts only if absent" logic. Deps: WS-E, WS-B.
- **WS-H — Catalog chop/subset utility (astroeasy).** Stream the mirror → write a
  smaller mirror in the same `MIRROR_DTYPE`, filtered by faint/bright magnitude and/or
  tile/region. Trivial per-tile mask; reused by every consumer (query, Rust reader,
  index/DB builders) and by a **local** API host. Deps: WS-A.
- **WS-I — tetra3 DB builder (astroeasy).** Bright Gaia slice (via WS-H) → tetra3
  `generate_database()` for the sensor's FoV → cached per `(fov, depth)`. Sibling of
  WS-B. Prototyped (`benchmarks/fast_solve/build_db.py` + `build_gaia_catalog.py`).
  Deps: WS-A, WS-H.
- **WS-J — Acceptance gate (likelihood model).** The §4 gate as a shared, solver-agnostic
  function: geometric log-odds + photometric (ZP-fit mag↔flux) + colour (BP−RP via
  `gaia_transforms`) + negative evidence + PM/epoch. Doubles as the cascade throttle
  (sensitivity → fewer escalations). **High priority — correctness backbone *and* speed
  lever.** Deps: WS-A.

---

## 9. Rollout & migration (keep astrometry.net working throughout)

1. **Skeleton + shadow.** Land the cascade with **only** T0 (senpai
   `wcs_refinement`) + T3/T4 (existing astrometry.net). Run it in **shadow** beside
   the current path; log per-tier hit rate, latency, and WCS agreement vs the
   astrometry.net baseline. *No behaviour change yet.* This alone is the "bankable
   free win" — most frames should resolve at T0.
2. **Add T1** (Python), shadow, measure how many T3/T4 calls it removes.
3. **Add T2** (custom index) + `/blind` characterization; demote stock indices to
   server-only.
4. **Flip default** to the cascade once agreement ≥ threshold on the golden set
   (§10). astrometry.net stays reachable as T3/T4.
5. **Port T0/T1 to Rust** (WS-D); swap the engine under the same `solve()` API.
6. **Trim the edge:** remove Docker/astrometry.net from sensor deployments; they
   keep only catalog + engine.

At every step the old path is one config flag away.

---

## 10. Validation & benchmarking

- **Harness exists:** `benchmarks/fast_solve/` (extract → solvers → metrics → run_bench)
  scores any engine vs senpai's ground-truth WCS; data localized to `benchmarks/data/`.
  Widen from DAO to more nights/sensors (~1900 runs available).
- **Golden set:** real frames across the fleet (DAO + others) with trusted WCS from
  full astrometry.net. The regression oracle.
- **Correctness:** cascade WCS vs golden — center offset (arcsec), scale error,
  rotation, per-star residual RMS. ACCEPT/REJECT confusion vs ground truth (the gate
  must not produce confident-wrong solves).
- **Coverage:** % solved at each tier; % escalated to astrometry.net; **zero**
  frames that the cascade fails but astrometry.net solves (your hard constraint).
- **Latency:** wall-clock per tier; end-to-end p50/p95; real-time-tracking budget.
- **Compactness:** disk footprint per deployment tier vs today's 35 + 17 GB.
- **Adversarial gate tests:** wrong prior, sparse/crowded fields, large rotation,
  parity flip, edge-of-tile, galactic-plane density — confirm REJECT, not
  confident-wrong.

---

## 11. Immediate, low-risk actions (no new architecture)

- Reclaim **~72 GB** by archiving/removing `gaia_g21/chunks/` (redundant post-ingest).
- In senpai today: pass `existing_wcs` / **propagate the previous-frame WCS** into
  the current `solve_field` path, and derive `--radius` from prior quality instead
  of the hardcoded 10°. This is Phase-0 of the cascade using only what ships now, and
  yields the measurement harness for everything above.
- Land the cfg-filtering follow-up from `fov-filtered-indices.md` §2 (explicit index
  lines vs `autoindex`) so even the astrometry.net tiers open only the needed scales.
- **Catalog chop utility (WS-H)** is a standalone ~30-line win: a per-tile magnitude
  mask over the mirror → a smaller `MIRROR_DTYPE` catalog. Lets a G18 sensor (or a
  local API host) carry ~13 GB instead of 72 GB, no other work required.

---

## 12. Risks & open questions

- **tetra3-as-T1: answered for point sources** (97% sidereal @ 1.6 ms, §3.3). Residual
  risk = the deep/dense tail (rate-track cold-start, 78%), which is **depth-bound, not
  tetra3-tunable** → handled by T3, or a T2 quad-matcher if it must be fast.
- **DAO pincushion: answered** — a non-issue at T1 (`distortion=None` locks fine, §3.3);
  senpai's SIP refine owns the accuracy.
- **Fast deep tail (T2) is genuinely hard** (§3.3): needs **quad-hashing on a deep cone**
  — pair-voting returns dense-field noise, tetra3 is too shallow. Decide build (native
  quad-matcher) vs accept (astrometry.net T3) from how often rate-track cold-starts occur
  and the latency budget. The boresight does **not** rescue it (it buys speed, not depth).
- **Narrow-FoV + no-prior cold-start** — untested (no narrow-FoV data yet); the tested
  analogue is the rate-track deep/dense tail. The §3.1 depth analysis stands.
- **Gate calibration per sensor** — thresholds live in the profile; need a principled
  default + per-sensor tuning during characterization.
- **Catalog completeness/epoch:** Gaia DR3 epoch 2016.0; apply proper motion
  (`pmra/pmdec` are in the mirror) for current-epoch projection — matters at high-PM
  stars and for tight residuals.
- **API service ownership:** who hosts it, auth, availability SLA for edge fall-through
  (the edge must degrade gracefully if the server is unreachable — e.g. queue for
  later characterization rather than drop the frame).
- **astrometry.net `build-astrometry-index` quirks:** runtime, memory, and FITS input
  schema for billion-row inputs; build per-tile to bound memory.
- **Rust ↔ Python boundary:** zero-copy detection arrays, error propagation, GIL
  release during solves.

---

## 13. Non-goals

- Reimplementing astrometry.net's all-sky blind solver (T2–T4 stay a.net).
- Replacing Gaia as the catalog, or re-deriving photometric transforms (reuse
  `gaia_transforms.py`).
- A new on-disk catalog format — the existing `MIRROR_DTYPE` mirror is the format.
- Per-sensor *spatial* index trimming as a headline win — sensors are fully steerable
  (all-sky), so the index win is scale-only; the real edge win is **T0/T1 native +
  no index**.

---

## 14. Phasing (ordered milestones)

- **Phase 0 — Bank the free win.** Reclaim chunks; propagate prev-frame WCS in
  senpai; cfg scale-filtering. Stand up the shadow-mode measurement harness.
- **Phase 1 — Catalog primitive.** WS-A (local mirror → astroeasy). senpai imports
  it. Online stays as fallback.
- **Phase 2 — Cascade skeleton (Python).** WS-C with T0 + T3/T4 and the gate; shadow
  vs baseline on the golden set.
- **Phase 3 — T1 (tetra3) ✓ + the gate.** tetra3 validated on DAO (§3.3, 97% sidereal).
  Build the **WS-J likelihood gate** (the priority — correctness + throttle); productionize
  WS-I DB builder + WS-H chop. Decide **T2** (native deep quad-matcher vs astrometry.net
  T3) from the measured tail rate + latency budget.
- **Phase 4 — Mirror API + characterization.** WS-E (`/cone`,`/index`,`/blind`,
  `/solve`), WS-G (profiles). Edge can now drop local astrometry.net.
- **Phase 5 — Rust core.** WS-D port of T0/T1 + catalog reader; swap under `solve()`;
  flip senpai default; trim edge deployments.

---

## Appendix — glossary

- **FoV** — field of view (angular width of the frame, deg).
- **iFoV** — instantaneous FoV = per-pixel angular scale (arcsec/px); the plate scale.
- **TAN+SIP** — gnomonic projection + Simple Imaging Polynomial distortion (the WCS
  model astrometry.net and senpai fit).
- **Tier (T0–T4)** — a rung of the escalation cascade (§3).
- **Gate** — the acceptance test (§4) every tier's output must pass.
- **Sensor profile** — measured geometry + gate thresholds + custom-index ref (§5).
- **Stock vs custom index** — stock = downloaded all-sky 4200/5200; custom =
  `build_custom_index()` from local Gaia, scale-matched to one sensor.
- **tetra3 / cedar-solve** — open-source (Apache-2.0) lost-in-space pattern-hash star
  solver; the proposed T1 engine. cedar-solve = the more active fork (smroid).
- **Pattern DB** — tetra3's FoV-specific hashed star-pattern catalog; a small derived
  artifact built from Gaia (WS-I), analogous to an index.
- **Distortion (tetra3)** — a single scalar radial term (barrel/pincushion at
  width/2), not SIP; enough to match through DAO pincushion, not to give precise
  astrometry (that's senpai's SIP refine).
- **Chop** — depth/region subsetting of the mirror into a smaller same-format catalog
  (WS-H).
- **Lost-in-space** — solving with no position prior (whole-sky search); tetra3's mode.
- **Log-odds gate** — the §4 likelihood ratio (matches-vs-chance, + photometry/colour);
  accept/reject *and* the cascade throttle.
- **Quad hashing** — 4-star geometric-hash matching (astrometry.net/tetra3); *specific*
  enough to survive dense fields, unlike pairwise voting.
- **Constrained matcher** — `constrained.py`: boresight-cone + roll/parity search + fit;
  the T0 prototype — great for sparse/prior'd frames, defeated by dense-deep (§3.3).
