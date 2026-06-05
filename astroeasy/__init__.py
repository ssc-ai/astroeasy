"""Astroeasy - Astrometry.net made easy.

A standalone Python package for plate-solving with astrometry.net.
Provides a clean, Pythonic API and easy Docker-based installation.

Example:
    >>> from astroeasy import (
    ...     AstrometryConfig,
    ...     Detection,
    ...     ImageMetadata,
    ...     solve_field,
    ... )
    >>> config = AstrometryConfig(
    ...     indices_path="/data/indices/5200-LITE",
    ...     docker_image="astrometry-cli",
    ... )
    >>> detections = [Detection(x=100, y=200, flux=1000), ...]
    >>> metadata = ImageMetadata(width=4096, height=4096)
    >>> result = solve_field(detections, metadata, config=config)
    >>> if result.success:
    ...     print(f"Solved! Center: {result.wcs.center_ra}, {result.wcs.center_dec}")
"""

from importlib.metadata import version

from astroeasy.catalog import query_gaia_field
from astroeasy.catalog.gaia import CatalogStar, get_field_bounds_from_wcs
from astroeasy.config import AstrometryConfig
from astroeasy.constants import AstrometryIndexSeries
from astroeasy.indices import download_indices, examine_indices, scales_for_fov
from astroeasy.models import (
    AggressiveSolveResult,
    Detection,
    ImageMetadata,
    MatchedStar,
    SolveResult,
    WCSResult,
    WCSStatus,
)
from astroeasy.plotting import plot_solved_field, zscale
from astroeasy.runner import (
    solve_field,
    solve_field_aggressive,
    solve_field_image,
    test_install,
)

__version__ = version("astroeasy")

__all__ = [
    # Configuration
    "AstrometryConfig",
    # Models
    "AggressiveSolveResult",
    "CatalogStar",
    "Detection",
    "ImageMetadata",
    "MatchedStar",
    "SolveResult",
    "WCSResult",
    "WCSStatus",
    # Constants
    "AstrometryIndexSeries",
    # Functions
    "solve_field",
    "solve_field_aggressive",
    "solve_field_image",
    "test_install",
    "examine_indices",
    "download_indices",
    "scales_for_fov",
    # Plotting
    "plot_solved_field",
    "zscale",
    # Catalog
    "query_gaia_field",
    "get_field_bounds_from_wcs",
]
