"""Acceptance gate: solver-agnostic likelihood test for a candidate WCS.

The cascade's correctness backbone (docs/catalog-native-solving-roadmap.md §4):
project the catalog through the candidate WCS, match to the detections, and
score matches *against the chance rate at the field's density*. A raw match
count fools dense fields (a wrong WCS racks up coincidences exactly as often
as chance predicts); the log-odds normalizes by that rate, so confident-wrong
solves REJECT and the cascade escalates instead of stopping on a bad answer.

v0 scores the geometric term (Poisson tail of matches vs chance + residual
RMS). The photometric / colour / negative-evidence terms from §4 extend
``score_wcs`` without changing its interface.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from astroeasy.cascade.catalog import Cone
from astroeasy.cascade.profile import GateThresholds
from astroeasy.cascade.wcsfit import match_nearest, project_catalog

logger = logging.getLogger(__name__)

# Gate the brightest detections only: faint detections are noisier and on
# streaked frames may include artifacts; the brightest-N carry the evidence.
MAX_GATE_DETECTIONS = 100


@dataclass
class GateResult:
    """Outcome of scoring one candidate WCS."""

    accepted: bool
    n_matches: int
    n_detections: int
    n_catalog_in_frame: int
    rms_px: float
    expected_chance_matches: float
    log_odds: float
    reason: str = ""
    det_idx: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    cat_idx: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))


def poisson_log_odds(n_matches: int, expected: float) -> float:
    """log-odds that ``n_matches`` arose from a real solution vs chance.

    Chernoff bound on P(Poisson(expected) >= n): the returned value is
    -log of that bound — i.e. how surprising the match count is under the
    chance hypothesis. <= 0 when the count is consistent with chance.
    """
    if n_matches <= 0:
        return -math.inf
    lam = max(expected, 1e-6)
    if n_matches <= lam:
        return 0.0
    return n_matches * math.log(n_matches / lam) - (n_matches - lam)


def score_wcs(
    awcs,
    det_x: np.ndarray,
    det_y: np.ndarray,
    cone: Cone,
    width: int,
    height: int,
    thresholds: GateThresholds,
) -> GateResult:
    """Score a candidate WCS against detections + catalog cone. ACCEPT or REJECT.

    Invariant (§4): when the evidence can't clear the thresholds this returns
    ``accepted=False`` — never a low-confidence accept.
    """
    n_det = min(len(det_x), MAX_GATE_DETECTIONS)
    det_x, det_y = det_x[:n_det], det_y[:n_det]

    cat_px, cat_py, cat_idx_map = project_catalog(awcs, cone, width, height)
    n_cat = len(cat_px)
    if n_cat == 0 or n_det == 0:
        return GateResult(False, 0, n_det, n_cat, math.inf, 0.0, -math.inf,
                          reason="no catalog stars project in-frame" if n_cat == 0 else "no detections")

    tol = thresholds.match_tolerance_px
    di, ci, dd = match_nearest(det_x, det_y, cat_px, cat_py, tol)
    n_match = len(di)
    rms = float(np.sqrt(np.mean(dd**2))) if n_match else math.inf

    # Chance rate: each detection sees n_cat * (pi tol^2 / area) expected
    # coincidences under a random alignment.
    expected = n_det * n_cat * math.pi * tol**2 / (width * height)
    log_odds = poisson_log_odds(n_match, expected)

    checks = [
        (n_match >= thresholds.min_matches, f"matches {n_match} < {thresholds.min_matches}"),
        (rms <= thresholds.max_rms_px, f"rms {rms:.2f}px > {thresholds.max_rms_px}"),
        (log_odds >= thresholds.min_log_odds, f"log_odds {log_odds:.1f} < {thresholds.min_log_odds}"),
    ]
    failed = [msg for ok, msg in checks if not ok]
    accepted = not failed

    logger.debug(
        "gate: n_match=%d/%d (cat in-frame %d) rms=%.2fpx expected_chance=%.2f log_odds=%.1f -> %s",
        n_match, n_det, n_cat, rms, expected, log_odds, "ACCEPT" if accepted else f"REJECT ({'; '.join(failed)})",
    )
    return GateResult(
        accepted=accepted, n_matches=n_match, n_detections=n_det, n_catalog_in_frame=n_cat,
        rms_px=rms, expected_chance_matches=expected, log_odds=log_odds,
        reason="; ".join(failed),
        det_idx=di, cat_idx=cat_idx_map[ci] if n_match else np.empty(0, dtype=int),
    )
