"""Tests for astroeasy.cascade: gate, tiers, escalation, profile, builders.

Built around a synthetic sky: a known truth WCS, catalog stars written into a
tiny on-disk mirror, and detections projected through the truth (+noise). The
gate must accept the truth and reject shifted/rotated impostors; the tiers must
recover the truth from degraded priors; the cascade must escalate on failure.
"""

import json
import math

import numpy as np
import pytest

from astroeasy.cascade.catalog import query_cone
from astroeasy.cascade.gate import score_wcs
from astroeasy.cascade.profile import GateThresholds, SensorProfile
from astroeasy.cascade.solve import solve as cascade_solve
from astroeasy.cascade.tier0 import refine_wcs, solve_constrained
from astroeasy.cascade.wcsfit import awcs_to_wcsresult, detections_to_arrays
from astroeasy.catalog.mirror import MIRROR_DTYPE, load_mirror_index
from astroeasy.models import Detection, ImageMetadata, SolveResult, WCSStatus

RA0, DEC0 = 150.0, 30.0
W = H = 2048
SCALE_ASEC = 2.0          # arcsec/px -> FoV ~1.14 deg
FOV_DEG = SCALE_ASEC * W / 3600.0
ROLL_DEG = 23.0
RNG = np.random.default_rng(42)


def make_wcs(ra0=RA0, dec0=DEC0, scale_asec=SCALE_ASEC, roll_deg=ROLL_DEG,
             crpix=(W / 2, H / 2)):
    """Truth TAN WCS, standard sky orientation (det CD < 0)."""
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra0, dec0]
    w.wcs.crpix = list(crpix)
    s = scale_asec / 3600.0
    t = math.radians(roll_deg)
    rot = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    w.wcs.cd = rot @ np.array([[-s, 0.0], [0.0, s]])
    return w


@pytest.fixture(scope="module")
def sky():
    """Catalog stars covering the field + margin; brightest ~G8, faintest G15.5."""
    n = 600
    radius = 1.2  # deg, > cone radius the cascade will request
    cosd = math.cos(math.radians(DEC0))
    ra = RA0 + RNG.uniform(-radius, radius, n) / cosd
    dec = DEC0 + RNG.uniform(-radius, radius, n)
    g = RNG.uniform(8.0, 15.5, n)
    sid = np.arange(1, n + 1, dtype="i8")
    return ra, dec, g, sid


@pytest.fixture(scope="module")
def mirror_dir(tmp_path_factory, sky):
    """One-tile mirror holding the synthetic sky."""
    d = tmp_path_factory.mktemp("mirror")
    ra, dec, g, sid = sky
    arr = np.zeros(len(ra), dtype=MIRROR_DTYPE)
    arr["source_id"], arr["ra"], arr["dec"], arr["g"] = sid, ra, dec, g
    arr["bp"] = g + 0.3
    arr["rp"] = g - 0.3
    arr.tofile(d / "tile.bin")
    index = {"tiles": {"t": {
        "file": "tile.bin",
        "ra_min": float(ra.min()), "ra_max": float(ra.max()),
        "dec_min": float(dec.min()), "dec_max": float(dec.max()),
    }}}
    (d / "index.json").write_text(json.dumps(index))
    load_mirror_index.cache_clear()
    return str(d)


@pytest.fixture(scope="module")
def truth_wcs():
    return make_wcs()


@pytest.fixture(scope="module")
def detections(sky, truth_wcs):
    """Detections = catalog stars projected through the truth + 0.3px noise."""
    ra, dec, g, _ = sky
    px, py = truth_wcs.all_world2pix(ra, dec, 0)
    ok = (px > 10) & (px < W - 10) & (py > 10) & (py < H - 10) & (g < 14.5)
    px, py, g = px[ok], py[ok], g[ok]
    px = px + RNG.normal(0, 0.3, len(px))
    py = py + RNG.normal(0, 0.3, len(py))
    flux = 10 ** (-0.4 * (g - 20.0))
    return [Detection(x=float(x), y=float(y), flux=float(f))
            for x, y, f in zip(px, py, flux, strict=True)]


@pytest.fixture(scope="module")
def cone(mirror_dir):
    return query_cone(mirror_dir, RA0, DEC0, 1.0, 16.0)


@pytest.fixture
def profile():
    return SensorProfile(
        sensor_id="synthetic",
        pixel_scale_arcsec=SCALE_ASEC,
        fov_degrees=FOV_DEG,
        scale_bounds_degrees=(0.9 * FOV_DEG, 1.1 * FOV_DEG),
        sip_order=0,  # synthetic sky has no distortion
        mag_depth_g=15.0,
    )


def _arrays(detections):
    return detections_to_arrays(detections)


class TestGate:
    def test_accepts_truth(self, detections, cone, truth_wcs):
        dx, dy = _arrays(detections)
        g = score_wcs(truth_wcs, dx, dy, cone, W, H, GateThresholds())
        assert g.accepted
        assert g.n_matches >= 30
        assert g.rms_px < 1.0
        assert g.log_odds > 50

    def test_rejects_shifted(self, detections, cone, truth_wcs):
        dx, dy = _arrays(detections)
        wrong = make_wcs(crpix=(W / 2 + 300, H / 2 - 250))
        g = score_wcs(wrong, dx, dy, cone, W, H, GateThresholds())
        assert not g.accepted

    def test_rejects_rotated(self, detections, cone):
        dx, dy = _arrays(detections)
        wrong = make_wcs(roll_deg=ROLL_DEG + 5.0)
        g = score_wcs(wrong, dx, dy, cone, W, H, GateThresholds())
        assert not g.accepted

    def test_rejects_empty_cone(self, detections, mirror_dir):
        dx, dy = _arrays(detections)
        empty = query_cone(mirror_dir, RA0 + 40.0, DEC0 - 20.0, 0.5, 16.0)
        g = score_wcs(make_wcs(ra0=RA0 + 40.0, dec0=DEC0 - 20.0), dx, dy, empty, W, H,
                      GateThresholds())
        assert not g.accepted


def _center_error_arcsec(awcs, truth_wcs):
    from astropy.coordinates import SkyCoord

    ra1, dec1 = awcs.all_pix2world(W / 2, H / 2, 0)
    ra2, dec2 = truth_wcs.all_pix2world(W / 2, H / 2, 0)
    c1 = SkyCoord(float(ra1), float(dec1), unit="deg")
    return c1.separation(SkyCoord(float(ra2), float(dec2), unit="deg")).arcsec


class TestTier0:
    def test_refine_from_perturbed_prior(self, detections, cone, truth_wcs):
        dx, dy = _arrays(detections)
        prior = make_wcs(roll_deg=ROLL_DEG + 1.0, crpix=(W / 2 + 25, H / 2 - 20))
        cand = refine_wcs(dx, dy, prior, cone, W, H, sip_order=0)
        assert cand.ok, cand.status
        assert _center_error_arcsec(cand.awcs, truth_wcs) < 1.0

    def test_refine_rejects_hopeless_prior(self, detections, cone):
        dx, dy = _arrays(detections)
        prior = make_wcs(ra0=RA0 + 3.0, dec0=DEC0 + 3.0)  # off-field
        cand = refine_wcs(dx, dy, prior, cone, W, H, sip_order=0)
        assert not cand.ok

    def test_constrained_from_boresight(self, detections, cone, truth_wcs):
        dx, dy = _arrays(detections)
        cand = solve_constrained(dx, dy, cone, W, H, scale_asec_per_px=SCALE_ASEC)
        assert cand.ok, cand.status
        refined = refine_wcs(dx, dy, cand.awcs, cone, W, H, sip_order=0)
        assert refined.ok
        assert _center_error_arcsec(refined.awcs, truth_wcs) < 1.0


class TestCascade:
    def test_t0_with_prior(self, detections, mirror_dir, profile, truth_wcs):
        prior = awcs_to_wcsresult(
            make_wcs(roll_deg=ROLL_DEG + 0.5, crpix=(W / 2 + 15, H / 2 - 10)), W, H)
        md = ImageMetadata(width=W, height=H)
        res = cascade_solve(detections, md, profile=profile, mirror_dir=mirror_dir,
                            prior_wcs=prior, tiers=("T0",))
        assert res.tier == "T0"
        assert res.solve.success
        assert res.solve.wcs is not None
        assert len(res.solve.matched_stars) >= 8
        assert _center_error_arcsec(res.solve.wcs.to_astropy_wcs(), truth_wcs) < 2.0

    def test_t0_from_boresight(self, detections, mirror_dir, profile, truth_wcs):
        md = ImageMetadata(width=W, height=H,
                           boresight_ra=RA0 + 0.05, boresight_dec=DEC0 - 0.04)
        res = cascade_solve(detections, md, profile=profile, mirror_dir=mirror_dir,
                            tiers=("T0",))
        assert res.tier == "T0"
        assert _center_error_arcsec(res.solve.wcs.to_astropy_wcs(), truth_wcs) < 2.0

    def test_escalates_to_dotnet(self, detections, mirror_dir, profile, truth_wcs,
                                 monkeypatch):
        """T0 fails (wrong boresight) -> astrometry.net backstop wins."""
        import astroeasy.runner as runner
        from astroeasy.config import AstrometryConfig

        def fake_solve_field(dets, md, config, existing_wcs=None):
            return SolveResult(success=True, status=WCSStatus.SUCCESS,
                               wcs=awcs_to_wcsresult(truth_wcs, W, H),
                               matched_stars=[], detections=dets, image_metadata=md)

        monkeypatch.setattr(runner, "solve_field", fake_solve_field)
        md = ImageMetadata(width=W, height=H,
                           boresight_ra=RA0 + 15.0, boresight_dec=DEC0 - 10.0)
        cfg = AstrometryConfig(indices_path="/nonexistent")
        res = cascade_solve(detections, md, profile=profile, mirror_dir=mirror_dir,
                            dotnet_config=cfg, tiers=("T0", "T3"))
        assert res.tier == "T3"
        assert res.solve.success
        t0_attempts = [a for a in res.attempts if a.tier == "T0"]
        assert t0_attempts and not t0_attempts[0].accepted

    def test_no_mirror_goes_straight_to_dotnet(self, detections, profile, truth_wcs,
                                               monkeypatch):
        import astroeasy.runner as runner
        from astroeasy.config import AstrometryConfig

        calls = []

        def fake_solve_field(dets, md, config, existing_wcs=None):
            calls.append(md)
            return SolveResult(success=True, status=WCSStatus.SUCCESS,
                               wcs=awcs_to_wcsresult(truth_wcs, W, H),
                               matched_stars=[], detections=dets, image_metadata=md)

        monkeypatch.setattr(runner, "solve_field", fake_solve_field)
        md = ImageMetadata(width=W, height=H, boresight_ra=RA0, boresight_dec=DEC0)
        res = cascade_solve(detections, md, profile=profile, mirror_dir=None,
                            dotnet_config=AstrometryConfig(indices_path="/x"),
                            tiers=("T0", "T1", "T3"))
        assert res.tier == "T3"
        assert len(calls) == 1

    def test_unmounted_mirror_degrades_to_dotnet(self, detections, profile, truth_wcs,
                                                 monkeypatch):
        """A configured-but-missing mirror (unplugged drive) must not crash:
        native tiers are skipped and the backstop carries the frame."""
        import astroeasy.runner as runner
        from astroeasy.config import AstrometryConfig

        def fake_solve_field(dets, md, config, existing_wcs=None):
            return SolveResult(success=True, status=WCSStatus.SUCCESS,
                               wcs=awcs_to_wcsresult(truth_wcs, W, H),
                               matched_stars=[], detections=dets, image_metadata=md)

        monkeypatch.setattr(runner, "solve_field", fake_solve_field)
        md = ImageMetadata(width=W, height=H, boresight_ra=RA0, boresight_dec=DEC0)
        res = cascade_solve(detections, md, profile=profile,
                            mirror_dir="/media/not/mounted/mirror",
                            dotnet_config=AstrometryConfig(indices_path="/x"),
                            tiers=("T0", "T1", "T3"))
        assert res.tier == "T3"
        assert res.solve.success

    def test_all_fail(self, detections, mirror_dir, profile):
        md = ImageMetadata(width=W, height=H,
                           boresight_ra=RA0 + 15.0, boresight_dec=DEC0 - 10.0)
        res = cascade_solve(detections, md, profile=profile, mirror_dir=mirror_dir,
                            tiers=("T0", "T1", "T3"))  # no dotnet config, T1 no DB
        assert res.tier is None
        assert not res.solve.success
        assert res.solve.status == WCSStatus.FAILED


class TestProfile:
    def test_yaml_roundtrip(self, tmp_path, profile):
        profile.tetra3_db_path = "/data/db.npz"
        profile.gate.min_log_odds = 12.5
        p = tmp_path / "sensor.yaml"
        profile.to_yaml(p)
        loaded = SensorProfile.from_yaml(p)
        assert loaded == profile
        assert isinstance(loaded.gate, GateThresholds)

    def test_capability_helpers(self, profile):
        assert profile.fov_estimate() == pytest.approx(FOV_DEG)
        assert profile.gate_faint_limit == 15.0


class TestBuilders:
    def test_write_tyc_main(self, mirror_dir, tmp_path):
        from astroeasy.cascade.tetra3db import write_tyc_main

        out = tmp_path / "tyc_main.dat"
        n = write_tyc_main(mirror_dir, out, mag_limit=12.0)
        lines = out.read_text().strip().splitlines()
        assert n == len(lines) > 0
        f = lines[0].split("|")
        assert f[1] == "1 1 1"
        assert float(f[5]) <= 12.0
        float(f[8]), float(f[9])  # RA/Dec parse

    def test_db_mag_heuristic(self):
        from astroeasy.cascade.tetra3db import db_mag_for_fov

        assert db_mag_for_fov(2.0) == 11.5
        assert db_mag_for_fov(0.4) == 15.0

    def test_write_star_table(self, mirror_dir, tmp_path):
        from astropy.io import fits

        from astroeasy.cascade.index_build import write_star_table

        out = tmp_path / "stars.fits"
        n = write_star_table(mirror_dir, out, depth_g=14.0)
        with fits.open(out) as hdul:
            data = hdul[1].data
            assert len(data) == n > 0
            assert data["MAG"].max() <= 14.0

    def test_build_custom_index_mocked(self, mirror_dir, tmp_path, monkeypatch):
        import subprocess

        from astroeasy.cascade import index_build

        def fake_run(cmd, **kwargs):
            # Emulate build-astrometry-index writing its output file.
            out = cmd[cmd.index("-o") + 1]
            work = kwargs.get("cwd")
            if work is None:  # docker form: find the host mount
                mount = next(a for a in cmd if a.startswith("--mount"))
                work = mount.split("source=")[1].split(",")[0]
            (index_build.Path(work) / out).write_bytes(b"FAKE")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(index_build.subprocess, "run", fake_run)
        paths = index_build.build_custom_index(
            mirror_dir, tmp_path / "idx", scales=[9, 10], depth_g=14.0)
        assert len(paths) == 2
        assert all(p.exists() for p in paths)
        # Idempotent: second call reuses without rebuilding.
        monkeypatch.setattr(index_build.subprocess, "run",
                            lambda *a, **k: pytest.fail("should not rebuild"))
        again = index_build.build_custom_index(
            mirror_dir, tmp_path / "idx", scales=[9, 10], depth_g=14.0)
        assert again == paths


class TestCharacterize:
    def test_from_detections(self, detections, mirror_dir, tmp_path, truth_wcs,
                             monkeypatch):
        import astroeasy.runner as runner
        from astroeasy.config import AstrometryConfig

        def fake_solve_field(dets, md, config, existing_wcs=None):
            assert md.boresight_ra is None  # characterization must solve BLIND
            return SolveResult(success=True, status=WCSStatus.SUCCESS,
                               wcs=awcs_to_wcsresult(truth_wcs, W, H),
                               matched_stars=[], detections=dets, image_metadata=md)

        monkeypatch.setattr(runner, "solve_field", fake_solve_field)
        from astroeasy.cascade.characterize import characterize_sensor

        md = ImageMetadata(width=W, height=H, boresight_ra=RA0, boresight_dec=DEC0)
        result = characterize_sensor(
            [(detections, md), (detections, md), (detections, md)],
            AstrometryConfig(indices_path="/stock"),
            sensor_id="synth", out_dir=tmp_path, mirror_dir=mirror_dir,
            build_db=False,
        )
        p = result.profile
        assert p.pixel_scale_arcsec == pytest.approx(SCALE_ASEC, rel=1e-3)
        assert p.fov_degrees == pytest.approx(FOV_DEG, rel=1e-3)
        assert p.parity == 1
        assert p.rotation_prior_deg is not None  # 3 stable frames -> rotation prior
        assert p.mag_depth_g is not None and 13.0 < p.mag_depth_g < 15.5
        assert (tmp_path / "synth.yaml").exists()
        loaded = SensorProfile.from_yaml(tmp_path / "synth.yaml")
        assert loaded.sensor_id == "synth"

    def test_too_few_frames_no_rotation_prior(self, detections, mirror_dir, tmp_path,
                                              truth_wcs, monkeypatch):
        """A 1-2 frame characterization must not emit a (degenerate) rotation prior."""
        import astroeasy.runner as runner
        from astroeasy.config import AstrometryConfig

        def fake_solve_field(dets, md, config, existing_wcs=None):
            return SolveResult(success=True, status=WCSStatus.SUCCESS,
                               wcs=awcs_to_wcsresult(truth_wcs, W, H),
                               matched_stars=[], detections=dets, image_metadata=md)

        monkeypatch.setattr(runner, "solve_field", fake_solve_field)
        from astroeasy.cascade.characterize import characterize_sensor

        md = ImageMetadata(width=W, height=H)
        result = characterize_sensor(
            [(detections, md)],  # single frame -> spread is degenerately 0
            AstrometryConfig(indices_path="/stock"),
            sensor_id="single", out_dir=tmp_path, mirror_dir=mirror_dir,
            build_db=False,
        )
        assert result.profile.rotation_prior_deg is None
