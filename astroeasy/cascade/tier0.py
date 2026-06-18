"""T0 — native refine from a prior (and the boresight-constrained matcher).

Two entry points, both catalog-only (no index, no astrometry.net):

- :func:`refine_wcs` — full prior WCS (previous frame / propagated mount model):
  project the cone, nearest-neighbour match, refit TAN+SIP, iterate. Trivial and
  fast; this is the rung that should carry every prior'd frame.
- :func:`solve_constrained` — boresight + plate scale only (roll/parity unknown):
  brute-force roll+parity with a translation vote on the bright cone, then
  recover matches against the full deep cone and fit. Promoted from
  ``benchmarks/fast_solve/constrained.py`` (validated on DAO sidereal frames:
  sub-arcsec to ~3" in <100 ms; defeated by dense-deep fields — that tail
  escalates, see roadmap §3.3).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from astroeasy.cascade.catalog import Cone, gnomonic
from astroeasy.cascade.wcsfit import fit_wcs, match_nearest, project_catalog

logger = logging.getLogger(__name__)


@dataclass
class TierCandidate:
    """A tier's candidate solution, pre-gate."""

    awcs: object | None         # astropy.wcs.WCS
    status: str                 # MATCH / NO_PRIOR / FEW_CAT / LOW_PEAK / FEW_MATCH / FIT_FAIL
    n_matches: int = 0

    @property
    def ok(self) -> bool:
        return self.awcs is not None


def refine_wcs(
    det_x: np.ndarray,
    det_y: np.ndarray,
    prior_awcs,
    cone: Cone,
    width: int,
    height: int,
    *,
    sip_order: int = 2,
    tolerances_px: tuple[float, ...] = (40.0, 12.0, 5.0),
    min_matches: int = 8,
    max_detections: int = 120,
) -> TierCandidate:
    """Iterative project → match → refit from a full prior WCS."""
    det_x, det_y = det_x[:max_detections], det_y[:max_detections]
    awcs = prior_awcs
    n_match = 0
    for it, tol in enumerate(tolerances_px):
        cat_px, cat_py, idx = project_catalog(awcs, cone, width, height)
        if len(cat_px) < min_matches:
            return TierCandidate(None, "FEW_CAT", n_matches=len(cat_px))
        di, ci, _ = match_nearest(det_x, det_y, cat_px, cat_py, tol)
        n_match = len(di)
        if n_match < min_matches:
            return TierCandidate(None, "FEW_MATCH", n_matches=n_match)
        sel = idx[ci]
        try:
            # Linear on the coarse pass; SIP only once matching has tightened.
            order = 0 if it < len(tolerances_px) - 1 else sip_order
            awcs = fit_wcs(det_x[di], det_y[di], cone.ra[sel], cone.dec[sel], sip_order=order)
        except Exception as e:  # noqa: BLE001 — astropy fit can fail many ways; tier must reject, not raise
            logger.debug("refine fit failed: %s", e)
            return TierCandidate(None, "FIT_FAIL", n_matches=n_match)
    return TierCandidate(awcs, "MATCH", n_matches=n_match)


def _rot(deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s], [s, c]])


def _peak(tf: np.ndarray, tol: float):
    """Densest translation cell over pair-translations tf (M,2): (count, center)."""
    if len(tf) == 0:
        return 0, np.zeros(2)
    cells = np.round(tf / tol).astype(np.int64) + 100000
    key = cells[:, 0] * 1_000_000 + cells[:, 1]
    uk, cnt = np.unique(key, return_counts=True)
    j = int(cnt.argmax())
    k = uk[j]
    return int(cnt[j]), np.array([(k // 1_000_000 - 100000) * tol, (k % 1_000_000 - 100000) * tol], float)


def solve_constrained(
    det_x: np.ndarray,
    det_y: np.ndarray,
    cone: Cone,
    width: int,
    height: int,
    *,
    scale_asec_per_px: float,
    rolls_deg: np.ndarray | None = None,
    parities: tuple[float, ...] = (1.0, -1.0),
    search_n: int = 1200,
    max_detections: int = 40,
    tol_coarse_px: float = 60.0,
    tol_fine_px: float = 25.0,
    min_matches: int = 10,
) -> TierCandidate:
    """Solve from a position + scale prior by brute-forcing roll and parity.

    ``rolls_deg`` restricts the coarse roll search (e.g. a rotation prior, or
    disambiguating a tetra3 roll); None searches the full circle at 2°.
    """
    if len(cone) < min_matches:
        return TierCandidate(None, "FEW_CAT", n_matches=len(cone))
    s_deg = scale_asec_per_px / 3600.0

    xi, eta = gnomonic(cone.ra, cone.dec, cone.ra0, cone.dec0)
    cat_px = np.column_stack([xi / s_deg, eta / s_deg])   # full deep cone, brightest-first
    search_px = cat_px[:search_n]                          # bright subset for the roll vote

    det_x, det_y = det_x[:max_detections], det_y[:max_detections]
    det = np.column_stack([det_x - width / 2.0, det_y - height / 2.0])

    def best_over(pxset, rolls, pars, tol):
        best = (-1, 1.0, 0.0, None)
        for parity in pars:
            cp = pxset * np.array([1.0, parity])
            for roll in rolls:
                cr = cp @ _rot(roll).T
                cnt, ctr = _peak((det[:, None, :] - cr[None, :, :]).reshape(-1, 2), tol)
                if cnt > best[0]:
                    best = (cnt, parity, roll, ctr)
        return best

    coarse_rolls = np.arange(0, 360, 2.0) if rolls_deg is None else np.asarray(rolls_deg)
    _, parity, roll_c, _ = best_over(search_px, coarse_rolls, parities, tol_coarse_px)
    cnt, parity, roll, t_est = best_over(
        search_px, np.arange(roll_c - 3, roll_c + 3, 0.25), (parity,), tol_fine_px
    )
    if cnt < min_matches:
        return TierCandidate(None, "LOW_PEAK", n_matches=int(cnt))

    # Recover matched pairs against the FULL deep cone at the winning transform.
    pred = (cat_px * np.array([1.0, parity])) @ _rot(roll).T + t_est
    di, ci, _ = match_nearest(det[:, 0], det[:, 1], pred[:, 0], pred[:, 1], tol_fine_px)
    if len(di) < min_matches:
        return TierCandidate(None, "FEW_MATCH", n_matches=len(di))

    try:
        awcs = fit_wcs(det_x[di], det_y[di], cone.ra[ci], cone.dec[ci], sip_order=0)
    except Exception as e:  # noqa: BLE001 — tier must reject, not raise
        logger.debug("constrained fit failed: %s", e)
        return TierCandidate(None, "FIT_FAIL", n_matches=len(di))
    return TierCandidate(awcs, "MATCH", n_matches=len(di))
