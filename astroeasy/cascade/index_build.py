"""WS-B: build custom scale-matched astrometry.net indices from the local mirror.

mirror → depth-cut FITS star table → ``build-astrometry-index`` (one run per
scale from ``scales_for_fov``) → an indices directory usable as
``AstrometryConfig(indices_path=…, indices_series="CUSTOM")``. The solver's
generated ``astrometry.cfg`` uses ``autoindex``, so the directory works as-is.

Runs ``build-astrometry-index`` inside the astrometry-cli Docker image (it is
built from source there) or a local install. Idempotent via a params sidecar.

v0 scope: all-sky single index per scale — right for the scale presets wide
sensors use (≳5). Very deep small-scale presets (<5) want HEALPix splitting
like the stock 4200 series; that's deliberately not built until a sensor
needs it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from astroeasy.catalog.mirror import load_mirror_index, read_tile

logger = logging.getLogger(__name__)

# Index IDs are arbitrary-but-unique ints baked into each file; keep custom
# builds in their own range, offset by scale so a multi-scale set stays unique.
CUSTOM_INDEX_ID_BASE = 7700


def write_star_table(mirror_dir: str, out_fits: Path | str, depth_g: float) -> int:
    """All-sky (RA, DEC, MAG) FITS bintable at G<=depth_g. Returns row count."""
    from astropy.io import fits

    index = load_mirror_index(mirror_dir)
    ras, decs, mags = [], [], []
    for meta in index["tiles"].values():
        arr = read_tile(mirror_dir, meta["file"])
        m = (np.isfinite(arr["g"]) & (arr["g"] <= depth_g)
             & np.isfinite(arr["ra"]) & np.isfinite(arr["dec"]))
        if not m.any():
            continue
        ras.append(arr["ra"][m].astype("f8"))
        decs.append(arr["dec"][m].astype("f8"))
        mags.append(arr["g"][m].astype("f4"))
    if not ras:
        return 0
    ra = np.concatenate(ras)
    dec = np.concatenate(decs)
    mag = np.concatenate(mags)
    if len(ra) > 100_000_000:
        raise ValueError(
            f"{len(ra)} stars at G<={depth_g} — too deep for a single all-sky build; "
            "lower depth_g (or wait for HEALPix-split builds)"
        )
    if len(ra) > 20_000_000:
        logger.warning("star table has %d rows — build-astrometry-index will be slow/RAM-heavy",
                       len(ra))
    hdu = fits.BinTableHDU.from_columns([
        fits.Column(name="RA", format="D", array=ra),
        fits.Column(name="DEC", format="D", array=dec),
        fits.Column(name="MAG", format="E", array=mag),
    ])
    hdu.writeto(out_fits, overwrite=True)
    logger.info("star table: %d stars (G<=%.1f) -> %s", len(ra), depth_g, out_fits)
    return len(ra)


def _run_build(work_dir: Path, args: list[str], docker_image: str | None) -> None:
    """Run build-astrometry-index with work_dir as cwd (host or container)."""
    if docker_image:
        cmd = [
            "docker", "run", "--rm", "--workdir=/home/starman",
            f"--mount=type=bind,source={work_dir},target=/home/starman",
            docker_image, "build-astrometry-index", *args,
        ]
    else:
        cmd = ["build-astrometry-index", *args]
    logger.info("running: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=None if docker_image else work_dir,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-15:])
        raise RuntimeError(f"build-astrometry-index failed (rc={proc.returncode}):\n{tail}")


def build_custom_index(
    mirror_dir: str,
    out_dir: Path | str,
    *,
    scales: list[int],
    depth_g: float,
    docker_image: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Build one index file per scale preset; return the index paths.

    Args:
        mirror_dir: Local Gaia mirror directory.
        out_dir: Output indices directory (use as ``indices_path`` with
            ``indices_series="CUSTOM"``).
        scales: astrometry.net scale presets (from ``scales_for_fov``).
        depth_g: Star-table depth (sensor's measured limiting G).
        docker_image: astrometry-cli image; None = local build-astrometry-index.
        force: Rebuild even if a matching set exists.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "custom_index.json"
    params = {"scales": sorted(scales), "depth_g": depth_g}

    expected = [out_dir / f"index-custom-{depth_g:.1f}-{s:02d}.fits" for s in sorted(scales)]
    if not force and all(p.exists() for p in expected):
        try:
            if json.loads(meta_path.read_text())["params"] == params:
                logger.info("custom index up to date: %s", out_dir)
                return expected
        except (OSError, KeyError, ValueError):
            pass

    with tempfile.TemporaryDirectory(prefix="astroeasy_index_") as tmp:
        work = Path(tmp)
        n = write_star_table(mirror_dir, work / "stars.fits", depth_g)
        if n == 0:
            raise ValueError(f"no stars at G<={depth_g} in mirror {mirror_dir}")
        built: list[Path] = []
        for scale in sorted(scales):
            out_name = f"index-custom-{depth_g:.1f}-{scale:02d}.fits"
            args = [
                "-i", "stars.fits", "-o", out_name,
                "-P", str(scale), "-S", "MAG",
                "-A", "RA", "-D", "DEC",
                "-I", str(CUSTOM_INDEX_ID_BASE + scale),
            ]
            _run_build(work, args, docker_image)
            target = out_dir / out_name
            shutil.move(str(work / out_name), target)  # tempdir may be on another filesystem
            built.append(target)
            logger.info("built %s (%.0f MB)", target, target.stat().st_size / 1e6)

    meta_path.write_text(json.dumps({"params": params, "n_stars": n}, indent=2))
    return built
