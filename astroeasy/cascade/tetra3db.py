"""WS-I: build a tetra3 pattern database straight from the local Gaia mirror.

mirror → bright slice in tetra3's ``tyc_main`` text format → ``generate_database``
for the sensor's FoV. No downloads; ~minutes; output is a few hundred MB .npz
(vs 32 GB of stock astrometry.net indices for the same job). Idempotent: keyed
by (max_fov, mag_limit) via a sidecar JSON next to the DB.

Format notes (hard-won, see benchmarks/RESULTS.md): tetra3's ``tyc_main``
parser needs field 1 = three space-separated ints (catalog ID, unused for
solving) and is built with ``epoch_proper_motion=None`` so blank PM fields are
accepted; mag at field 5, RA(deg) at 8, Dec(deg) at 9.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import numpy as np

from astroeasy.catalog.mirror import MIRROR_DTYPE, load_mirror_index

logger = logging.getLogger(__name__)


def db_mag_for_fov(fov_degrees: float) -> float:
    """Default pattern-DB depth for a field width (roadmap §3.1 density table).

    Aims for a comfortable margin over ~10-30 matchable stars per field;
    narrow fields need fainter stars to have any pattern at all.
    """
    if fov_degrees >= 1.5:
        return 11.5
    if fov_degrees >= 0.7:
        return 13.0
    if fov_degrees >= 0.3:
        return 15.0
    return 16.0


def write_tyc_main(mirror_dir: str, out_path: Path | str, mag_limit: float) -> int:
    """Emit the all-sky G<=mag_limit slice in tyc_main layout. Returns star count."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    index = load_mirror_index(mirror_dir)
    tiles = index["tiles"]
    written = 0
    with open(out_path, "w") as fh:
        for i, meta in enumerate(tiles.values()):
            arr = np.fromfile(os.path.join(mirror_dir, meta["file"]), dtype=MIRROR_DTYPE)
            m = (np.isfinite(arr["g"]) & (arr["g"] <= mag_limit)
                 & np.isfinite(arr["ra"]) & np.isfinite(arr["dec"]))
            if not m.any():
                continue
            a = arr[m]
            lines = [
                f"|1 1 1||||{g:.3f}|||{r:.6f}|{d:.6f}|||||"
                for r, d, g in zip(a["ra"].tolist(), a["dec"].tolist(), a["g"].tolist(), strict=True)
            ]
            fh.write("\n".join(lines) + "\n")
            written += int(m.sum())
            if (i + 1) % 500 == 0:
                logger.info("tyc_main slice: tile %d/%d, %d stars", i + 1, len(tiles), written)
    logger.info("tyc_main slice done: %d stars (G<=%.1f) -> %s", written, mag_limit, out_path)
    return written


def build_tetra3_db(
    mirror_dir: str,
    out_path: Path | str,
    *,
    max_fov_deg: float,
    mag_limit: float | None = None,
    min_fov_deg: float | None = None,
    force: bool = False,
) -> Path:
    """Build (or reuse) a tetra3 pattern DB for a sensor's FoV from the mirror.

    Args:
        mirror_dir: Local Gaia mirror directory.
        out_path: Output DB path (".npz" appended if missing).
        max_fov_deg: Sensor field width (give the measured value + ~2% margin).
        mag_limit: Catalog depth; default from :func:`db_mag_for_fov`.
        min_fov_deg: None = single-FoV DB (smallest/fastest).
        force: Rebuild even if a matching DB exists.

    Returns:
        Path to the .npz database.
    """
    out_path = Path(out_path)
    if out_path.suffix != ".npz":
        out_path = out_path.with_suffix(out_path.suffix + ".npz")
    meta_path = out_path.with_suffix(".json")
    if mag_limit is None:
        mag_limit = db_mag_for_fov(max_fov_deg)

    params = {"max_fov_deg": max_fov_deg, "min_fov_deg": min_fov_deg, "mag_limit": mag_limit}
    if out_path.exists() and not force:
        try:
            if json.loads(meta_path.read_text())["params"] == params:
                logger.info("tetra3 DB up to date: %s", out_path)
                return out_path
        except (OSError, KeyError, ValueError):
            pass
        logger.info("tetra3 DB exists with different/unknown params — rebuilding %s", out_path)

    try:
        from astroeasy._vendor.tetra3 import Tetra3
    except ImportError as err:
        raise ImportError(
            "tetra3 dependencies missing — install with: pip install astroeasy[cascade]"
        ) from err

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="astroeasy_tetra3_") as tmp:
        catalog_dat = Path(tmp) / "tyc_main.dat"
        n_stars = write_tyc_main(mirror_dir, catalog_dat, mag_limit)
        if n_stars == 0:
            raise ValueError(f"no stars at G<={mag_limit} in mirror {mirror_dir}")
        t3 = Tetra3(load_database=None)
        logger.info("generating tetra3 DB (max_fov=%.2f° mag<=%.1f, %d stars) — takes minutes…",
                    max_fov_deg, mag_limit, n_stars)
        t3.generate_database(
            max_fov=max_fov_deg, min_fov=min_fov_deg,
            save_as=str(out_path.with_suffix("")),
            star_catalog=str(catalog_dat), star_max_magnitude=mag_limit,
            epoch_proper_motion=None,
        )
    meta_path.write_text(json.dumps({"params": params, "n_catalog_stars": n_stars}, indent=2))
    logger.info("tetra3 DB saved: %s (%.0f MB)", out_path, out_path.stat().st_size / 1e6)
    return out_path
