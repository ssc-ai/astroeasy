"""Offline Gaia queries against a local HEALPix-tiled binary mirror.

The mirror is a directory of fixed-width binary tiles (one file per HEALPix
level-4 tile; records are ``MIRROR_DTYPE``) plus an ``index.json`` mapping each
tile file to its RA/Dec bounding box. Tile membership derives from the Gaia
``source_id`` (``hpx = source_id >> 51``), so the format needs no healpy at
query time. Queries read only the tiles whose bbox overlaps the requested box —
sub-second per field, no network.

This is the offline counterpart to :func:`astroeasy.catalog.gaia.query_gaia_field`
(see ``docs/catalog-native-solving-roadmap.md``, WS-A). The dtype must stay
byte-compatible with existing mirrors on disk — change it only with a migration.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from typing import Any

import numpy as np

from astroeasy.catalog.gaia import CatalogStar

logger = logging.getLogger(__name__)

# ra/dec MUST be f8 (f4 loses arcsec precision at RA~300 deg).
MIRROR_DTYPE = np.dtype([
    ("source_id", "i8"),
    ("ra", "f8"), ("dec", "f8"),      # degrees
    ("g", "f4"), ("bp", "f4"), ("rp", "f4"),
    ("pmra", "f4"), ("pmdec", "f4"),  # mas/yr
])


@functools.lru_cache(maxsize=8)
def load_mirror_index(mirror_dir: str) -> dict[str, Any]:
    """Load and cache a mirror's ``index.json`` (tile file → RA/Dec bbox)."""
    with open(os.path.join(mirror_dir, "index.json")) as fh:
        return json.load(fh)


def read_tile(mirror_dir: str, filename: str) -> np.ndarray:
    """Read one mirror tile as a ``MIRROR_DTYPE`` array, with integrity checks.

    ``np.fromfile`` would raise a bare ``FileNotFoundError`` for a tile that
    ``index.json`` references but isn't on disk (unmounted/incomplete mirror),
    and would *silently* drop a trailing partial record from a truncated tile.
    Both fail confusingly far from the cause, so we surface them here: a clear
    error for a missing tile, a warning for a non-record-aligned size.
    """
    path = os.path.join(mirror_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Gaia mirror tile missing: {path} — index.json references it but it is "
            "not on disk (mirror unmounted or incomplete?)"
        )
    itemsize = MIRROR_DTYPE.itemsize
    remainder = os.path.getsize(path) % itemsize
    if remainder:
        logger.warning(
            "Gaia mirror tile %s is not a multiple of the %d-byte record "
            "(%d trailing bytes) — reading whole records only (truncated/corrupt?)",
            filename, itemsize, remainder,
        )
    return np.fromfile(path, dtype=MIRROR_DTYPE)


def _ra_subranges(min_ra: float, max_ra: float) -> list[tuple[float, float]]:
    """Normalize the RA box to [0,360), splitting across the 0/360 seam if needed."""
    lo, hi = np.mod(min_ra, 360.0), np.mod(max_ra, 360.0)
    if lo <= hi:
        return [(lo, hi)]
    return [(lo, 360.0), (0.0, hi)]  # wraps 0


def query_mirror_box(
    min_ra: float,
    max_ra: float,
    min_dec: float,
    max_dec: float,
    *,
    mirror_dir: str,
    faint_limit: float | None = None,
    bright_limit: float | None = None,
    max_rows: int | None = None,
) -> np.ndarray:
    """Raw mirror rows within an RA/Dec box, optionally magnitude-cut and capped.

    The primitive every consumer builds on (cascade tiers, index/DB builders,
    senpai's star-dict wrapper): returns a ``MIRROR_DTYPE`` structured array
    (RA/Dec in degrees), not converted objects.

    Args:
        min_ra: Minimum right ascension in degrees (box may wrap RA=0).
        max_ra: Maximum right ascension in degrees.
        min_dec: Minimum declination in degrees.
        max_dec: Maximum declination in degrees.
        mirror_dir: Mirror directory (tiles + index.json).
        faint_limit: Keep stars with G <= this (None = no faint cut; stars with
            NaN G survive only when both limits are None).
        bright_limit: Keep stars with G >= this (None = no bright cut).
        max_rows: If set and more rows match, keep the brightest ``max_rows``
            (bounds memory on dense galactic-plane fields).

    Returns:
        Structured array with dtype ``MIRROR_DTYPE``.
    """
    index = load_mirror_index(mirror_dir)
    ra_ranges = _ra_subranges(min_ra, max_ra)

    # Pick tiles whose bbox overlaps the (possibly seam-split) box.
    chosen = []
    for meta in index["tiles"].values():
        if meta["dec_max"] < min_dec or meta["dec_min"] > max_dec:
            continue
        if any(not (meta["ra_max"] < r0 or meta["ra_min"] > r1) for r0, r1 in ra_ranges):
            chosen.append(meta)
    if not chosen:
        logger.info("Gaia mirror: no tiles overlap the requested box")
        return np.empty(0, dtype=MIRROR_DTYPE)

    parts = [read_tile(mirror_dir, m["file"]) for m in chosen]
    a = np.concatenate(parts) if len(parts) > 1 else parts[0]

    mask = (a["dec"] >= min_dec) & (a["dec"] <= max_dec)
    if faint_limit is not None:
        mask &= a["g"] <= faint_limit
    if bright_limit is not None:
        mask &= a["g"] >= bright_limit
    ra_mask = np.zeros(len(a), dtype=bool)
    for r0, r1 in ra_ranges:
        ra_mask |= (a["ra"] >= r0) & (a["ra"] <= r1)
    a = a[mask & ra_mask]

    n_raw = len(a)
    if max_rows is not None and n_raw > max_rows:
        idx = np.argpartition(a["g"], max_rows)[:max_rows]
        a = a[idx]

    logger.info(
        "Gaia mirror: %d stars from %d tiles (box RA[%.3f,%.3f] Dec[%.3f,%.3f])%s",
        len(a), len(chosen), min_ra, max_ra, min_dec, max_dec,
        f" [capped from {n_raw} to brightest-{max_rows}]" if max_rows is not None and n_raw > max_rows else "",
    )
    return a


def query_gaia_field_local(
    min_ra: float,
    max_ra: float,
    min_dec: float,
    max_dec: float,
    *,
    mirror_dir: str,
    faint_limit: float = 18.0,
    bright_limit: float = -5.0,
    max_stars: int = 10000,
) -> list[CatalogStar]:
    """Offline drop-in for :func:`astroeasy.catalog.gaia.query_gaia_field`.

    Same return shape (brightest-first ``CatalogStar`` list, capped at
    ``max_stars``), served from the local mirror instead of the Gaia TAP service.
    """
    rows = query_mirror_box(
        min_ra, max_ra, min_dec, max_dec,
        mirror_dir=mirror_dir,
        faint_limit=faint_limit,
        bright_limit=bright_limit,
        max_rows=max_stars,
    )
    order = np.argsort(rows["g"])
    return [
        CatalogStar(
            ra=float(r["ra"]),
            dec=float(r["dec"]),
            magnitude=float(r["g"]),
            source_id=str(int(r["source_id"])),
            catalog="Gaia",
        )
        for r in rows[order]
    ]
