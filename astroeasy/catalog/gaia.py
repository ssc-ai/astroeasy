"""Gaia catalog query utilities."""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CatalogStar:
    """A star from a catalog query."""

    ra: float  # Right ascension in degrees
    dec: float  # Declination in degrees
    magnitude: float  # Primary magnitude (G-band for Gaia)
    source_id: str | None = None
    catalog: str = "Gaia"


def query_gaia_field(
    min_ra: float,
    max_ra: float,
    min_dec: float,
    max_dec: float,
    faint_limit: float = 18.0,
    bright_limit: float = -5.0,
    max_stars: int = 10000,
    mirror_dir: str | None = None,
) -> list[CatalogStar]:
    """Query Gaia DR3 catalog for stars within RA/Dec bounds.

    Args:
        min_ra: Minimum right ascension in degrees.
        max_ra: Maximum right ascension in degrees.
        min_dec: Minimum declination in degrees.
        max_dec: Maximum declination in degrees.
        faint_limit: Faint magnitude limit (default 18.0).
        bright_limit: Bright magnitude limit (default -5.0).
        max_stars: Maximum number of stars to return (default 10000).
        mirror_dir: If set, serve the query from a local mirror directory
            (see astroeasy.catalog.mirror) instead of the online TAP service.

    Returns:
        List of CatalogStar objects.

    Raises:
        ImportError: If astroquery is not installed (online queries only).
    """
    if mirror_dir is not None:
        from astroeasy.catalog.mirror import query_gaia_field_local

        return query_gaia_field_local(
            min_ra, max_ra, min_dec, max_dec,
            mirror_dir=mirror_dir,
            faint_limit=faint_limit,
            bright_limit=bright_limit,
            max_stars=max_stars,
        )

    try:
        from astroquery.gaia import Gaia
    except ImportError as err:
        raise ImportError(
            "astroquery is required for Gaia catalog queries. "
            "Install with: pip install astroquery"
        ) from err

    # Normalize RA to [0, 360) range
    min_ra_norm = np.mod(min_ra, 360.0)
    max_ra_norm = np.mod(max_ra, 360.0)

    # Check if field crosses RA = 0/360 boundary
    ra_span = max_ra_norm - min_ra_norm
    crosses_zero = ra_span > 180.0 or (min_ra_norm > max_ra_norm and ra_span < 180.0)

    try:
        if crosses_zero:
            logger.info(
                f"Querying Gaia (crosses RA=0): "
                f"RA=[{min_ra_norm:.3f}, 360] U [0, {max_ra_norm:.3f}], "
                f"Dec=[{min_dec:.3f}, {max_dec:.3f}], G<{faint_limit}"
            )

            # Query first range: high RA values (near 360)
            adql1 = f"""
            SELECT TOP {max_stars}
                source_id, ra, dec, phot_g_mean_mag as G
            FROM gaiadr3.gaia_source
            WHERE phot_g_mean_mag BETWEEN {bright_limit} AND {faint_limit}
            AND ra >= {min_ra_norm} AND ra <= 360.0
            AND dec BETWEEN {min_dec} AND {max_dec}
            ORDER BY phot_g_mean_mag ASC
            """

            # Query second range: low RA values (near 0)
            adql2 = f"""
            SELECT TOP {max_stars}
                source_id, ra, dec, phot_g_mean_mag as G
            FROM gaiadr3.gaia_source
            WHERE phot_g_mean_mag BETWEEN {bright_limit} AND {faint_limit}
            AND ra >= 0.0 AND ra <= {max_ra_norm}
            AND dec BETWEEN {min_dec} AND {max_dec}
            ORDER BY phot_g_mean_mag ASC
            """

            result1 = Gaia.launch_job(adql1).get_results()
            result2 = Gaia.launch_job(adql2).get_results()

            # Combine results
            from astropy.table import vstack

            results_to_combine = []
            if result1 is not None and len(result1) > 0:
                results_to_combine.append(result1)
            if result2 is not None and len(result2) > 0:
                results_to_combine.append(result2)

            if results_to_combine:
                result = vstack(results_to_combine)
            else:
                result = None
        else:
            logger.info(
                f"Querying Gaia: RA=[{min_ra_norm:.3f}, {max_ra_norm:.3f}], "
                f"Dec=[{min_dec:.3f}, {max_dec:.3f}], G<{faint_limit}"
            )

            adql = f"""
            SELECT TOP {max_stars}
                source_id, ra, dec, phot_g_mean_mag as G
            FROM gaiadr3.gaia_source
            WHERE phot_g_mean_mag BETWEEN {bright_limit} AND {faint_limit}
            AND ra BETWEEN {min_ra_norm} AND {max_ra_norm}
            AND dec BETWEEN {min_dec} AND {max_dec}
            ORDER BY phot_g_mean_mag ASC
            """

            result = Gaia.launch_job(adql).get_results()

        if result is None or len(result) == 0:
            logger.info("No stars found in Gaia query")
            return []

        logger.info(f"Gaia returned {len(result)} stars")

        # Convert to CatalogStar objects
        stars = []
        for row in result:
            if "G" in result.colnames and not np.isnan(row["G"]):
                mag = float(row["G"])
            else:
                mag = faint_limit

            stars.append(
                CatalogStar(
                    ra=float(row["ra"]),
                    dec=float(row["dec"]),
                    magnitude=mag,
                    source_id=str(row["source_id"]),
                    catalog="Gaia",
                )
            )

        # Sort by magnitude (brightest first) and limit
        stars.sort(key=lambda s: s.magnitude)
        return stars[:max_stars]

    except Exception as e:
        logger.error(f"Gaia query failed: {e}")
        return []


def get_field_bounds_from_wcs(wcs, width: int, height: int) -> tuple[float, float, float, float]:
    """Get RA/Dec bounds from a WCS and image dimensions.

    Args:
        wcs: Astropy WCS object.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Tuple of (min_ra, max_ra, min_dec, max_dec) in degrees.
    """
    import warnings

    # Sample corners and edges
    x_coords = [0, width - 1, 0, width - 1, width // 2, width // 2, 0, width - 1]
    y_coords = [0, 0, height - 1, height - 1, 0, height - 1, height // 2, height // 2]

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        coords = wcs.pixel_to_world(x_coords, y_coords)

    ra_values = coords.ra.deg
    dec_values = coords.dec.deg

    # Filter valid values
    valid = np.isfinite(ra_values) & np.isfinite(dec_values)
    ra_values = ra_values[valid]
    dec_values = dec_values[valid]

    if len(ra_values) == 0:
        raise ValueError("Could not compute valid RA/Dec bounds from WCS")

    # Handle RA wraparound
    ra_rad = np.deg2rad(ra_values)
    ra_center = np.rad2deg(np.arctan2(np.mean(np.sin(ra_rad)), np.mean(np.cos(ra_rad))))
    if ra_center < 0:
        ra_center += 360

    ra_normalized = ra_values - ra_center
    ra_normalized = np.where(ra_normalized > 180, ra_normalized - 360, ra_normalized)
    ra_normalized = np.where(ra_normalized < -180, ra_normalized + 360, ra_normalized)

    min_ra = ra_center + np.min(ra_normalized)
    max_ra = ra_center + np.max(ra_normalized)

    # Normalize to [0, 360)
    min_ra = np.mod(min_ra, 360.0)
    max_ra = np.mod(max_ra, 360.0)

    min_dec = np.min(dec_values)
    max_dec = np.max(dec_values)

    return min_ra, max_ra, min_dec, max_dec
