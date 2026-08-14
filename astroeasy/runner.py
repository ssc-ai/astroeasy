"""High-level solve_field API for astrometry.net plate solving."""

import logging
from dataclasses import replace
from pathlib import Path

from astroeasy.config import AstrometryConfig
from astroeasy.dotnet.docker import test_dotnet_install as test_docker_install
from astroeasy.dotnet.local import test_dotnet_install as test_local_install
from astroeasy.dotnet.runner import solve_field as solve_field_dotnet
from astroeasy.dotnet.runner import solve_field_image as solve_field_image_dotnet
from astroeasy.models import (
    AggressiveSolveResult,
    Detection,
    ImageMetadata,
    SolveResult,
    WCSResult,
    WCSStatus,
)

logger = logging.getLogger(__name__)


def solve_field(
    detections: list[Detection],
    metadata: ImageMetadata,
    config: AstrometryConfig,
    existing_wcs: WCSResult | None = None,
) -> SolveResult:
    """Solve astrometry for detected sources.

    This is the main entry point for plate solving. It accepts a list of
    detections with pixel coordinates and returns a WCS solution if successful.

    Args:
        detections: List of detected sources with x, y, and optional flux.
        metadata: Image metadata including width, height, and optional hints.
        config: Astrometry configuration including indices path and settings.
        existing_wcs: Optional existing WCS to verify/refine instead of blind solving.

    Returns:
        SolveResult containing:
        - success: Whether the solve was successful
        - status: Detailed status (SUCCESS, FAILED, INSUFFICIENT_SOURCES, TIMEOUT)
        - wcs: WCS solution if successful
        - matched_stars: Catalog stars matched to detections
        - detections: Original detections submitted
        - image_metadata: Image metadata used

    Example:
        >>> from astroeasy import AstrometryConfig, Detection, ImageMetadata, solve_field
        >>> config = AstrometryConfig(
        ...     indices_path="/data/indices/5200-LITE",
        ...     docker_image="astrometry-cli",
        ... )
        >>> detections = [Detection(x=100, y=200, flux=1000), ...]
        >>> metadata = ImageMetadata(width=4096, height=4096)
        >>> result = solve_field(detections, metadata, config)
        >>> if result.success:
        ...     print(f"Solved! Center: {result.wcs.center_ra}, {result.wcs.center_dec}")
    """
    # Check minimum sources
    if len(detections) < config.min_sources_for_attempt:
        logger.error(
            f"{len(detections)} sources (less than {config.min_sources_for_attempt}) "
            "found, skipping astrometry"
        )
        return SolveResult(
            success=False,
            status=WCSStatus.INSUFFICIENT_SOURCES,
            wcs=None,
            matched_stars=[],
            detections=detections,
            image_metadata=metadata,
        )

    # Dispatch to dotnet runner
    return solve_field_dotnet(detections, metadata, config, existing_wcs)


def solve_field_image(
    image_path: Path | str,
    config: AstrometryConfig,
    existing_wcs: WCSResult | None = None,
) -> SolveResult:
    """Solve astrometry directly from a FITS image file.

    This lets astrometry.net handle source extraction internally, which is more
    robust than simple threshold-based extraction. Use this when you have a FITS
    image file and want astrometry.net to find sources automatically.

    Args:
        image_path: Path to the FITS image file.
        config: Astrometry configuration including indices path and settings.
        existing_wcs: Optional existing WCS to verify/refine instead of blind solving.

    Returns:
        SolveResult containing:
        - success: Whether the solve was successful
        - status: Detailed status (SUCCESS, FAILED, TIMEOUT)
        - wcs: WCS solution if successful
        - matched_stars: Catalog stars matched to detections
        - detections: Empty list (sources extracted by astrometry.net)
        - image_metadata: Image metadata extracted from FITS

    Example:
        >>> from astroeasy import AstrometryConfig, solve_field_image
        >>> config = AstrometryConfig(
        ...     indices_path="/data/indices/5200-LITE",
        ...     docker_image="astrometry-cli",
        ... )
        >>> result = solve_field_image("observation.fits", config)
        >>> if result.success:
        ...     print(f"Solved! Center: {result.wcs.center_ra}, {result.wcs.center_dec}")
    """
    if isinstance(image_path, str):
        image_path = Path(image_path)

    return solve_field_image_dotnet(image_path, config, existing_wcs)


def solve_field_aggressive(
    detections: list[Detection],
    metadata: ImageMetadata,
    config: AstrometryConfig,
    max_sources_sequence: list[int] | None = None,
    existing_wcs: WCSResult | None = None,
) -> AggressiveSolveResult:
    """Solve astrometry by trying multiple max_sources values until one succeeds.

    This function tries each max_sources value in sequence, stopping on the first
    successful solve. This is useful when the optimal number of sources is unknown.

    Args:
        detections: List of detected sources with x, y, and optional flux.
        metadata: Image metadata including width, height, and optional hints.
        config: Astrometry configuration including indices path and settings.
        max_sources_sequence: List of max_sources values to try in order.
            Defaults to [25, 50, 100, 200, 500].
        existing_wcs: Optional existing WCS to verify/refine instead of blind solving.

    Returns:
        AggressiveSolveResult containing:
        - result: The final SolveResult (successful or last failed attempt)
        - attempts: List of (max_sources, success) tuples for each attempt
        - successful_max_sources: The max_sources value that succeeded, or None

    Example:
        >>> from astroeasy import AstrometryConfig, Detection, ImageMetadata
        >>> from astroeasy import solve_field_aggressive
        >>> config = AstrometryConfig(
        ...     indices_path="/data/indices/5200-LITE",
        ...     docker_image="astrometry-cli",
        ... )
        >>> detections = [Detection(x=100, y=200, flux=1000), ...]
        >>> metadata = ImageMetadata(width=4096, height=4096)
        >>> result = solve_field_aggressive(detections, metadata, config)
        >>> if result.result.success:
        ...     print(f"Solved with {result.successful_max_sources} sources!")
    """
    if max_sources_sequence is None:
        max_sources_sequence = [25, 50, 100, 200, 500]

    attempts: list[tuple[int, bool]] = []
    last_result: SolveResult | None = None

    for max_sources in max_sources_sequence:
        logger.info(f"Aggressive solve: trying max_sources={max_sources}")

        # Vary only max_sources: `replace` copies every other field, so a setting the
        # caller configured cannot be silently reverted to its default on this path.
        modified_config = replace(config, max_sources=max_sources)

        result = solve_field(detections, metadata, modified_config, existing_wcs)
        attempts.append((max_sources, result.success))
        last_result = result

        if result.success:
            logger.info(f"Aggressive solve succeeded with max_sources={max_sources}")
            return AggressiveSolveResult(
                result=result,
                attempts=attempts,
                successful_max_sources=max_sources,
            )

    logger.warning("Aggressive solve failed with all max_sources values")
    assert last_result is not None  # At least one attempt was made
    return AggressiveSolveResult(
        result=last_result,
        attempts=attempts,
        successful_max_sources=None,
    )


def test_install(docker_image: str | None = None) -> bool:
    """Test if astrometry.net is properly installed.

    Args:
        docker_image: Docker image name to test. If None, tests local installation.

    Returns:
        True if solve-field is available and working, False otherwise.
    """
    if docker_image is None:
        return test_local_install()
    else:
        return test_docker_install(docker_image)
