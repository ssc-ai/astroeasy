"""WS-G: sensor characterization — the bridge from "new, unknown sensor" to
"runs the fast tiers".

The chain (roadmap §5): raw sidereal frames → astrometry.net FULL BLIND solve
(stock indices, hints stripped — this is the one place blind is the point) →
measure FoV / pixel scale / rotation / parity / limiting magnitude → persist a
``SensorProfile`` → build the fast-solve artifacts if absent (tetra3 pattern
DB, and optionally a custom scale-matched astrometry.net index).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from astroeasy.cascade.profile import SensorProfile
from astroeasy.cascade.wcsfit import detections_to_arrays, match_nearest, project_catalog
from astroeasy.config import AstrometryConfig
from astroeasy.models import ImageMetadata, SolveResult

logger = logging.getLogger(__name__)

# Depth used for the depth-measurement cone — deeper than any expected sensor.
DEPTH_MEASURE_FAINT_G = 18.0


@dataclass
class FrameMeasurement:
    """Geometry measured from one blind-solved frame."""

    pixel_scale_arcsec: float
    fov_degrees: float
    roll_deg: float
    parity: int
    n_matched: int
    depth_g: float | None = None


@dataclass
class CharacterizationResult:
    profile: SensorProfile
    frames: list[FrameMeasurement] = field(default_factory=list)
    n_failed: int = 0
    tetra3_db_path: Path | None = None
    custom_index_dir: Path | None = None


def _measure_frame(result: SolveResult, mirror_dir: str | None) -> FrameMeasurement:
    wcs = result.wcs
    width = result.image_metadata.width
    cd11, cd12, cd21, cd22 = wcs.cd_matrix
    det_cd = cd11 * cd22 - cd12 * cd21
    measurement = FrameMeasurement(
        pixel_scale_arcsec=wcs.pixel_scale,
        fov_degrees=wcs.pixel_scale * width / 3600.0,
        # North-angle proxy; only its frame-to-frame *spread* matters here.
        roll_deg=math.degrees(math.atan2(cd12, cd11)) % 360.0,
        # +1 = standard sky orientation (det CD < 0). Telemetry: the cascade
        # searches both parities rather than trusting this sign convention.
        parity=1 if det_cd < 0 else -1,
        n_matched=len(result.matched_stars),
    )
    if mirror_dir and result.detections:
        measurement.depth_g = _measure_depth(result, mirror_dir)
    elif result.matched_stars:
        mags = [m.magnitude for m in result.matched_stars if m.magnitude is not None]
        if mags:
            measurement.depth_g = float(max(mags))
    return measurement


def _measure_depth(result: SolveResult, mirror_dir: str) -> float | None:
    """Faintest G reliably matched by the detections (p95 of matched mags)."""
    from astroeasy.cascade.catalog import query_cone

    md = result.image_metadata
    fov = result.wcs.pixel_scale * md.width / 3600.0
    cone = query_cone(mirror_dir, result.wcs.center_ra, result.wcs.center_dec,
                      0.75 * fov, DEPTH_MEASURE_FAINT_G)
    det_x, det_y = detections_to_arrays(result.detections)
    awcs = result.wcs.to_astropy_wcs()
    px, py, idx = project_catalog(awcs, cone, md.width, md.height, max_stars=20000)
    di, ci, _ = match_nearest(det_x, det_y, px, py, 4.0)
    if len(ci) < 5:
        return None
    return float(np.percentile(cone.g[idx[ci]], 95))


def characterize_sensor(
    frames: list,
    dotnet_config: AstrometryConfig,
    *,
    sensor_id: str,
    out_dir: Path | str,
    mirror_dir: str | None = None,
    build_db: bool = True,
    db_mag: float | None = None,
    build_index: bool = False,
    index_depth: float | None = None,
    sip_order: int = 2,
) -> CharacterizationResult:
    """Blind-solve frames, measure the sensor, persist profile + artifacts.

    Args:
        frames: FITS paths (astrometry.net extracts sources), or
            ``(detections, metadata)`` tuples.
        dotnet_config: astrometry.net config with STOCK indices and generous
            scale bounds — this is the blind/T4 measurement pass.
        sensor_id: Name for the profile (e.g. "dao01").
        out_dir: Where the profile YAML and built artifacts go.
        mirror_dir: Local Gaia mirror; enables depth measurement and is
            required for artifact builds.
        build_db: Build the tetra3 pattern DB if absent (needs mirror_dir).
        db_mag: tetra3 DB depth override (default: FoV-driven heuristic).
        build_index: Also build a custom scale-matched astrometry.net index
            (WS-B; needs mirror_dir + dotnet_config.docker_image).
        index_depth: Custom-index depth override (default: measured depth).
        sip_order: SIP order recorded in the profile for the native refine.
    """
    from astroeasy.runner import solve_field, solve_field_image

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    measured: list[FrameMeasurement] = []
    n_failed = 0
    for frame in frames:
        if isinstance(frame, (str, Path)):
            result = solve_field_image(Path(frame), dotnet_config)
        else:
            detections, metadata = frame
            blind_md = ImageMetadata(width=metadata.width, height=metadata.height)
            result = solve_field(list(detections), blind_md, dotnet_config)
        if not (result.success and result.wcs is not None):
            n_failed += 1
            logger.warning("characterize: blind solve failed (%s)", result.status)
            continue
        m = _measure_frame(result, mirror_dir)
        logger.info("characterize: frame solved — scale %.4f\"/px, fov %.4f°, roll %.1f°, "
                    "parity %+d, depth %s",
                    m.pixel_scale_arcsec, m.fov_degrees, m.roll_deg, m.parity,
                    f"G{m.depth_g:.1f}" if m.depth_g else "n/a")
        measured.append(m)

    if not measured:
        raise RuntimeError(
            f"characterization failed: 0/{len(frames)} frames blind-solved — "
            "check indices_path covers wide scales and frames are sidereal"
        )

    scales = np.array([m.pixel_scale_arcsec for m in measured])
    fovs = np.array([m.fov_degrees for m in measured])
    rolls = np.radians([m.roll_deg for m in measured])
    depths = [m.depth_g for m in measured if m.depth_g is not None]
    parity = int(np.sign(np.median([m.parity for m in measured])) or 1)

    # Rotation prior only if the field rotation is actually stable frame-to-frame
    # (equatorial mounts); alt-az/steerable sensors rotate per-frame -> None.
    c, s = np.mean(np.cos(rolls)), np.mean(np.sin(rolls))
    roll_spread_deg = math.degrees(math.sqrt(max(0.0, -2 * math.log(max(1e-12, math.hypot(c, s))))))
    rotation_prior = float(math.degrees(math.atan2(s, c)) % 360.0) if roll_spread_deg < 2.0 else None

    fov = float(np.median(fovs))
    profile = SensorProfile(
        sensor_id=sensor_id,
        pixel_scale_arcsec=float(np.median(scales)),
        fov_degrees=fov,
        scale_bounds_degrees=(float(round(0.95 * fovs.min(), 4)),
                              float(round(1.05 * fovs.max(), 4))),
        rotation_prior_deg=rotation_prior,
        parity=parity,
        sip_order=sip_order,
        mag_depth_g=float(np.median(depths)) if depths else None,
    )

    result = CharacterizationResult(profile=profile, frames=measured, n_failed=n_failed)

    # Build fast-solve artifacts only if absent (idempotent via param sidecars).
    if build_db:
        if mirror_dir is None:
            logger.warning("characterize: build_db requested but no mirror_dir — skipped")
        else:
            from astroeasy.cascade.tetra3db import build_tetra3_db

            db_path = build_tetra3_db(
                mirror_dir, out_dir / f"{sensor_id}_tetra3.npz",
                max_fov_deg=round(1.02 * fov, 3), mag_limit=db_mag,
            )
            profile.tetra3_db_path = str(db_path)
            result.tetra3_db_path = db_path

    if build_index:
        if mirror_dir is None:
            logger.warning("characterize: build_index requested but no mirror_dir — skipped")
        else:
            from astroeasy.cascade.index_build import build_custom_index
            from astroeasy.indices import scales_for_fov

            index_dir = out_dir / f"{sensor_id}_index"
            build_custom_index(
                mirror_dir, index_dir,
                scales=scales_for_fov(fov),
                depth_g=index_depth or (profile.mag_depth_g or 16.0),
                docker_image=dotnet_config.docker_image,
            )
            profile.custom_index_path = str(index_dir)
            result.custom_index_dir = index_dir

    profile_path = out_dir / f"{sensor_id}.yaml"
    profile.to_yaml(profile_path)
    logger.info("characterize: profile saved -> %s (from %d/%d frames)",
                profile_path, len(measured), len(frames))
    return result
