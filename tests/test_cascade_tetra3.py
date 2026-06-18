"""End-to-end tetra3 (T1) tests — opt-in, local-only.

The rest of the cascade suite runs with ``tetra3_db_path=None`` (T1 skipped),
so the tetra3 lost-in-space path is otherwise unexercised. These tests build a
real tetra3 pattern DB from a synthetic all-sky mirror and drive a frame
through it, covering: DB build + idempotent reuse, a direct ``solve_tetra3``
lock, the full cascade accepting via T1, and the gate rejecting garbage so T1
never false-accepts.

They are gated OFF by default (building a tetra3 DB takes ~10s and needs the
``[cascade]`` extra). Run them explicitly:

    ASTROEASY_TETRA3_TESTS=1 pytest tests/test_cascade_tetra3.py -v

Everything is seeded, so a passing run is deterministic.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

from astroeasy.models import Detection, ImageMetadata

# --- opt-in gate -------------------------------------------------------------
_OPT_IN = os.environ.get("ASTROEASY_TETRA3_TESTS") == "1"


def _cascade_deps_present() -> bool:
    try:
        import PIL  # noqa: F401
        import scipy  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not (_OPT_IN and _cascade_deps_present()),
    reason="set ASTROEASY_TETRA3_TESTS=1 (and install astroeasy[cascade]) to run; "
    "builds a tetra3 DB (~10s)",
)

# --- synthetic sky -----------------------------------------------------------
W = H = 1024
FOV_DEG = 4.0
SCALE_ASEC = FOV_DEG * 3600.0 / W      # exact field width -> 4.0 deg
RA0, DEC0, ROLL_DEG = 80.0, -15.0, 33.0
MAG_LIMIT = 9.5
N_BASE = 9000                          # all-sky base -> valid tetra3 lattice
N_CLUSTER = 26                         # guaranteed in-frame stars at the truth center


def _truth_wcs():
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [RA0, DEC0]
    w.wcs.crpix = [W / 2, H / 2]
    s = SCALE_ASEC / 3600.0
    t = math.radians(ROLL_DEG)
    rot = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    w.wcs.cd = rot @ np.array([[-s, 0.0], [0.0, s]])
    return w


@pytest.fixture(scope="module")
def synthetic_sky():
    """All-sky base catalog + a deterministic cluster around the truth center."""
    rng = np.random.default_rng(7)
    u = rng.uniform(-1, 1, N_BASE)
    ra = rng.uniform(0, 360, N_BASE)
    dec = np.degrees(np.arcsin(u))
    g = rng.uniform(4.0, MAG_LIMIT, N_BASE)

    cosd = math.cos(math.radians(DEC0))
    half = FOV_DEG * 0.45
    cra = RA0 + rng.uniform(-half, half, N_CLUSTER) / cosd
    cdec = DEC0 + rng.uniform(-half, half, N_CLUSTER)
    cg = rng.uniform(5.0, MAG_LIMIT - 0.5, N_CLUSTER)

    ra = np.concatenate([ra, cra])
    dec = np.concatenate([dec, cdec])
    g = np.concatenate([g, cg])
    return ra, dec, g


@pytest.fixture(scope="module")
def mirror_dir(tmp_path_factory, synthetic_sky):
    from astroeasy.catalog.mirror import MIRROR_DTYPE, load_mirror_index

    ra, dec, g = synthetic_sky
    d = tmp_path_factory.mktemp("t3_mirror")
    arr = np.zeros(len(ra), dtype=MIRROR_DTYPE)
    arr["source_id"] = np.arange(1, len(ra) + 1, dtype="i8")
    arr["ra"], arr["dec"], arr["g"] = ra, dec, g
    arr["bp"], arr["rp"] = g + 0.3, g - 0.3
    arr.tofile(d / "tile.bin")
    (d / "index.json").write_text(json.dumps({"tiles": {"t": {
        "file": "tile.bin", "ra_min": 0.0, "ra_max": 360.0,
        "dec_min": -90.0, "dec_max": 90.0,
    }}}))
    load_mirror_index.cache_clear()
    return str(d)


@pytest.fixture(scope="module")
def tetra3_db(tmp_path_factory, mirror_dir):
    from astroeasy.cascade.tetra3db import build_tetra3_db

    out = tmp_path_factory.mktemp("t3_db") / "synth.npz"
    return build_tetra3_db(mirror_dir, out, max_fov_deg=FOV_DEG * 1.05, mag_limit=MAG_LIMIT)


@pytest.fixture(scope="module")
def frame_detections(synthetic_sky):
    """Catalog stars projected through the truth WCS (+ sub-pixel noise)."""
    ra, dec, g = synthetic_sky
    w = _truth_wcs()
    px, py = w.all_world2pix(ra, dec, 0)
    ok = (px > 5) & (px < W - 5) & (py > 5) & (py < H - 5)
    px, py, g = px[ok], py[ok], g[ok]
    rng = np.random.default_rng(2)
    px = px + rng.normal(0, 0.15, len(px))
    py = py + rng.normal(0, 0.15, len(py))
    flux = 10 ** (-0.4 * (g - 15.0))
    return [Detection(x=float(x), y=float(y), flux=float(f))
            for x, y, f in zip(px, py, flux, strict=True)]


def _profile(tetra3_db):
    from astroeasy.cascade.profile import SensorProfile

    return SensorProfile(
        sensor_id="synthetic_t3",
        pixel_scale_arcsec=SCALE_ASEC,
        # scale_bounds (not fov_degrees) so fov_max_error is generous enough for
        # the single-scale synthetic DB.
        scale_bounds_degrees=(0.9 * FOV_DEG, 1.1 * FOV_DEG),
        sip_order=0,                # synthetic sky has no distortion
        mag_depth_g=MAG_LIMIT,
        tetra3_db_path=str(tetra3_db),
    )


# --- tests -------------------------------------------------------------------
def test_db_built_and_idempotent(tetra3_db, mirror_dir):
    from astroeasy.cascade.tetra3db import build_tetra3_db

    assert tetra3_db.exists()
    assert tetra3_db.with_suffix(".json").exists()
    sidecar = json.loads(tetra3_db.with_suffix(".json").read_text())
    assert sidecar["params"]["mag_limit"] == MAG_LIMIT
    assert sidecar["n_catalog_stars"] > 0
    # Second call with matching params reuses (no rebuild) and returns same path.
    again = build_tetra3_db(mirror_dir, tetra3_db, max_fov_deg=FOV_DEG * 1.05,
                            mag_limit=MAG_LIMIT)
    assert again == tetra3_db


def test_solve_tetra3_locks(tetra3_db, frame_detections):
    from astroeasy.cascade.tier1 import solve_tetra3
    from astroeasy.cascade.wcsfit import detections_to_arrays

    det_x, det_y = detections_to_arrays(frame_detections)
    lock = solve_tetra3(det_x, det_y, W, H, str(tetra3_db),
                        fov_estimate=FOV_DEG, fov_max_error=0.3)
    assert lock.ok, f"tetra3 did not lock: {lock.status}"
    cosd = math.cos(math.radians(DEC0))
    sep_arcsec = math.hypot((lock.ra - RA0) * cosd, lock.dec - DEC0) * 3600.0
    assert sep_arcsec < 120.0          # coarse center, refined by tier0 downstream
    assert lock.fov_deg == pytest.approx(FOV_DEG, abs=0.3)


def test_cascade_accepts_via_t1(tetra3_db, frame_detections, mirror_dir):
    from astroeasy.cascade.solve import solve as cascade_solve

    md = ImageMetadata(width=W, height=H)   # no boresight -> truly lost-in-space
    result = cascade_solve(
        frame_detections, md,
        profile=_profile(tetra3_db),
        mirror_dir=mirror_dir,
        tiers=("T1",),
    )
    assert result.tier == "T1"
    assert result.solve.success
    t1 = next(a for a in result.attempts if a.tier == "T1")
    assert t1.accepted and t1.gate is not None and t1.gate.accepted
    assert t1.gate.n_matches >= 8          # many genuine catalog matches
    assert t1.gate.rms_px < 1.0            # tight fit, not a chance alignment

    # Verify the field is actually recovered: the SOLVED WCS at the image-center
    # pixel must land on the truth center. (We compare the image center, not
    # WCSResult.center_ra — fit_wcs_from_points puts CRVAL at the matched-star
    # centroid, which legitimately sits a fraction of the FoV from frame center.)
    solved = result.solve.wcs.to_astropy_wcs()
    sra, sdec = solved.all_pix2world(W / 2, H / 2, 0)
    cosd = math.cos(math.radians(DEC0))
    sep_arcsec = math.hypot((float(sra) - RA0) * cosd, float(sdec) - DEC0) * 3600.0
    assert sep_arcsec < 60.0


def test_cascade_t1_rejects_garbage(tetra3_db, mirror_dir):
    """Random detections must not false-accept; with only T1 the cascade fails."""
    from astroeasy.cascade.solve import solve as cascade_solve

    rng = np.random.default_rng(99)
    noise = [Detection(x=float(x), y=float(y), flux=1.0)
             for x, y in rng.uniform(0, W, size=(40, 2))]
    md = ImageMetadata(width=W, height=H)
    result = cascade_solve(
        noise, md,
        profile=_profile(tetra3_db),
        mirror_dir=mirror_dir,
        tiers=("T1",),
    )
    assert result.tier is None
    assert not result.solve.success
