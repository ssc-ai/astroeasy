"""T1 — tetra3 (cedar-solve) lost-in-space pattern match.

Wraps the vendored tetra3 (``astroeasy/_vendor``) behind a small API: load a
pattern DB (cached per path — the .npz is hundreds of MB), solve from
detection centroids, return the coarse lock (center RA/Dec, FoV, roll).

Convention gotcha baked in from the DAO benchmark (benchmarks/RESULTS.md):
astroeasy detections use FITS convention (y from the bottom); tetra3 wants
(y, x) from the TOP-left — so y is flipped by default. The returned roll is
therefore *not* trusted directly: the cascade re-derives orientation by
running the constrained matcher around the locked center (tier0), which also
absorbs any parity/convention mismatch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter

import numpy as np

logger = logging.getLogger(__name__)

_T3_CACHE: dict[str, object] = {}

# tetra3 numeric status codes -> readable names.
T3_STATUS = {1: "MATCH_FOUND", 2: "NO_MATCH", 3: "TIMEOUT", 4: "CANCELLED", 5: "TOO_FEW"}


def load_tetra3(db_path: str):
    """A Tetra3 instance with ``db_path`` loaded (cached per path)."""
    import os

    # tetra3 resolves relative paths against its own package data/ dir.
    db_path = os.path.abspath(db_path)
    t3 = _T3_CACHE.get(db_path)
    if t3 is None:
        try:
            from astroeasy._vendor.tetra3 import Tetra3
        except ImportError as err:
            raise ImportError(
                "tetra3 dependencies missing — install with: pip install astroeasy[cascade]"
            ) from err
        logger.info("loading tetra3 DB %s", db_path)
        t3 = Tetra3(load_database=db_path)
        _T3_CACHE[db_path] = t3
    return t3


@dataclass
class Tetra3Lock:
    """Coarse T1 lock: image-center sky position + field geometry."""

    ok: bool
    status: str
    ra: float | None = None        # image center, degrees
    dec: float | None = None
    fov_deg: float | None = None
    roll_deg: float | None = None  # tetra3 convention — disambiguated downstream
    n_matches: int | None = None
    prob: float | None = None
    t_solve_ms: float | None = None


def solve_tetra3(
    det_x: np.ndarray,
    det_y: np.ndarray,
    width: int,
    height: int,
    db_path: str,
    *,
    fov_estimate: float | None = None,
    fov_max_error: float | None = 0.5,
    max_stars: int = 30,
    solve_timeout_ms: int = 2000,
    distortion: float = 0.0,
    flip_y: bool = True,
) -> Tetra3Lock:
    """Pattern-match the brightest centroids against the DB. ~ms when it locks."""
    t3 = load_tetra3(db_path)
    n = min(len(det_x), max_stars)
    yy = (height - det_y[:n]) if flip_y else det_y[:n]
    centroids = np.column_stack([yy, det_x[:n]]).astype(float)  # (y, x) top-left

    t0 = perf_counter()
    try:
        res = t3.solve_from_centroids(
            centroids, size=(height, width),
            fov_estimate=fov_estimate, fov_max_error=fov_max_error,
            distortion=distortion, solve_timeout=solve_timeout_ms,
        ) or {}
    except Exception as e:  # noqa: BLE001 — tier must reject, not raise
        logger.debug("tetra3 solve raised: %s", e)
        return Tetra3Lock(False, "ERROR")
    status = T3_STATUS.get(res.get("status"), str(res.get("status")))
    if res.get("RA") is None:
        return Tetra3Lock(False, status, t_solve_ms=res.get("T_solve"))
    logger.debug("tetra3 lock: ra=%.4f dec=%.4f fov=%.3f in %.1fms",
                 res["RA"], res["Dec"], res.get("FOV") or -1, (perf_counter() - t0) * 1e3)
    return Tetra3Lock(
        True, status, ra=float(res["RA"]), dec=float(res["Dec"]),
        fov_deg=res.get("FOV"), roll_deg=res.get("Roll"),
        n_matches=res.get("Matches"), prob=res.get("Prob"), t_solve_ms=res.get("T_solve"),
    )
