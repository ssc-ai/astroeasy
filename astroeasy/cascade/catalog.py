"""Catalog cones for the cascade: deep star slices around a sky position.

Thin layer over :mod:`astroeasy.catalog.mirror` that turns the box-query
primitive into the cone the tiers and the gate consume, with per-process tile
caching so consecutive frames on the same field are nearly free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from astroeasy.catalog.mirror import query_mirror_box


@dataclass
class Cone:
    """A catalog slice around (ra0, dec0): parallel arrays, brightest-first."""

    ra0: float
    dec0: float
    radius_deg: float
    faint_limit: float
    ra: np.ndarray          # degrees
    dec: np.ndarray         # degrees
    g: np.ndarray           # Gaia G
    source_id: np.ndarray   # int64

    def __len__(self) -> int:
        return len(self.ra)

    def covers(self, ra0: float, dec0: float, radius_deg: float, faint_limit: float) -> bool:
        """True if this cone already contains the requested cone."""
        dd = abs(self.dec0 - dec0)
        cosd = max(0.02, math.cos(math.radians((self.dec0 + dec0) / 2.0)))
        dr = abs(((self.ra0 - ra0 + 180.0) % 360.0) - 180.0) * cosd
        sep = math.hypot(dd, dr)
        return sep + radius_deg <= self.radius_deg and faint_limit <= self.faint_limit


def query_cone(
    mirror_dir: str,
    ra0: float,
    dec0: float,
    radius_deg: float,
    faint_limit: float,
    *,
    max_rows: int = 300_000,
) -> Cone:
    """Deep cone from the mirror, brightest-first.

    Queries the bounding box (RA-padded by 1/cos dec; full RA ring near the
    poles), then cuts to the true angular radius.
    """
    if abs(dec0) + radius_deg >= 88.0:
        min_ra, max_ra = 0.0, 360.0
    else:
        pad = radius_deg / max(0.02, math.cos(math.radians(abs(dec0) + radius_deg)))
        min_ra, max_ra = ra0 - pad, ra0 + pad
    rows = query_mirror_box(
        min_ra, max_ra,
        max(-90.0, dec0 - radius_deg), min(90.0, dec0 + radius_deg),
        mirror_dir=mirror_dir, faint_limit=faint_limit, max_rows=max_rows,
    )

    # Cut the box to the cone and sort brightest-first.
    cosd = np.cos(np.radians(dec0))
    dra = (rows["ra"] - ra0 + 180.0) % 360.0 - 180.0
    sep2 = (dra * cosd) ** 2 + (rows["dec"] - dec0) ** 2
    rows = rows[sep2 <= radius_deg**2]
    rows = rows[np.argsort(rows["g"])]

    return Cone(
        ra0=ra0, dec0=dec0, radius_deg=radius_deg, faint_limit=faint_limit,
        ra=rows["ra"].astype(float), dec=rows["dec"].astype(float),
        g=rows["g"].astype(float), source_id=rows["source_id"].copy(),
    )


def gnomonic(ra, dec, ra0: float, dec0: float):
    """Tangent-plane (xi, eta) in degrees about (ra0, dec0). xi ~ East, eta ~ North."""
    ra_r, dec_r = np.radians(ra), np.radians(dec)
    ra0_r, dec0_r = math.radians(ra0), math.radians(dec0)
    cosc = math.sin(dec0_r) * np.sin(dec_r) + math.cos(dec0_r) * np.cos(dec_r) * np.cos(ra_r - ra0_r)
    xi = np.cos(dec_r) * np.sin(ra_r - ra0_r) / cosc
    eta = (math.cos(dec0_r) * np.sin(dec_r) - math.sin(dec0_r) * np.cos(dec_r) * np.cos(ra_r - ra0_r)) / cosc
    return np.degrees(xi), np.degrees(eta)
