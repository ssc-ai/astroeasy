"""Shared geometry for the native tiers: matching, WCS fitting, conversions."""

from __future__ import annotations

import logging

import numpy as np

from astroeasy.models import Detection, MatchedStar, WCSResult

logger = logging.getLogger(__name__)


def detections_to_arrays(detections: list[Detection], max_n: int | None = None):
    """(x, y) arrays brightest-first (unknown flux sorts last)."""
    dets = sorted(detections, key=lambda d: (d.flux is None, -(d.flux or 0.0)))
    if max_n is not None:
        dets = dets[:max_n]
    x = np.array([d.x for d in dets], dtype=float)
    y = np.array([d.y for d in dets], dtype=float)
    return x, y


def project_catalog(awcs, cone, width: int, height: int, *, max_stars: int = 3000):
    """Project a Cone through an astropy WCS; return in-frame (px, py, idx).

    ``idx`` indexes back into the cone arrays. Cone is brightest-first, so
    capping at ``max_stars`` keeps the brightest.
    """
    n = min(len(cone), max_stars)
    if n == 0:
        return np.empty(0), np.empty(0), np.empty(0, dtype=int)
    with np.errstate(invalid="ignore"):
        px, py = awcs.all_world2pix(cone.ra[:n], cone.dec[:n], 0, quiet=True)
    ok = np.isfinite(px) & np.isfinite(py) & (px >= 0) & (px < width) & (py >= 0) & (py < height)
    return px[ok], py[ok], np.flatnonzero(ok)


def match_nearest(det_x, det_y, cat_px, cat_py, tol_px: float):
    """Greedy nearest-neighbour match, unique on both sides.

    Returns (det_idx, cat_idx, dist) arrays for pairs within ``tol_px``.
    """
    if len(det_x) == 0 or len(cat_px) == 0:
        return np.empty(0, dtype=int), np.empty(0, dtype=int), np.empty(0)
    d2 = (det_x[:, None] - cat_px[None, :]) ** 2 + (det_y[:, None] - cat_py[None, :]) ** 2
    nearest = d2.argmin(axis=1)
    dist = np.sqrt(d2[np.arange(len(det_x)), nearest])
    order = np.argsort(dist)
    used_cat: set[int] = set()
    di, ci, dd = [], [], []
    for i in order:
        if dist[i] >= tol_px:
            break
        j = int(nearest[i])
        if j in used_cat:
            continue
        used_cat.add(j)
        di.append(int(i))
        ci.append(j)
        dd.append(float(dist[i]))
    return np.array(di, dtype=int), np.array(ci, dtype=int), np.array(dd)


def fit_wcs(det_x, det_y, sky_ra, sky_dec, *, sip_order: int = 0):
    """Fit a TAN(+SIP) WCS through matched (pixel, sky) points.

    SIP needs enough points to constrain the polynomial; degrade to linear
    when matches are scarce.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astropy.wcs.utils import fit_wcs_from_points

    sky = SkyCoord(np.asarray(sky_ra) * u.deg, np.asarray(sky_dec) * u.deg)
    sip = sip_order if (sip_order > 0 and len(det_x) >= 8 * sip_order) else None
    return fit_wcs_from_points((np.asarray(det_x), np.asarray(det_y)), sky,
                               projection="TAN", sip_degree=sip)


def awcs_to_wcsresult(awcs, width: int, height: int) -> WCSResult:
    """astropy WCS → astroeasy WCSResult (CD-matrix form, astrometry.net-style keys)."""
    hdr = awcs.to_header(relax=True)
    raw = dict(hdr)
    # WCSResult and downstream consumers (senpai's WCSModel) expect the CD form.
    cd = awcs.pixel_scale_matrix  # PC*CDELT collapsed, deg/px
    for k in list(raw):
        if k.startswith("PC") or k.startswith("CDELT"):
            del raw[k]
    raw["CD1_1"], raw["CD1_2"] = float(cd[0, 0]), float(cd[0, 1])
    raw["CD2_1"], raw["CD2_2"] = float(cd[1, 0]), float(cd[1, 1])
    raw["IMAGEW"], raw["IMAGEH"] = int(width), int(height)
    return WCSResult.from_fits_header(raw)


def matched_stars_from_gate(cone, gate_result, det_x, det_y) -> list[MatchedStar]:
    """Build MatchedStar records from accepted gate matches."""
    out = []
    for di, ci in zip(gate_result.det_idx, gate_result.cat_idx, strict=True):
        out.append(MatchedStar(
            x=float(det_x[di]), y=float(det_y[di]),
            ra=float(cone.ra[ci]), dec=float(cone.dec[ci]),
            magnitude=float(cone.g[ci]), catalog="Gaia",
            catalog_id=str(int(cone.source_id[ci])),
        ))
    return out
