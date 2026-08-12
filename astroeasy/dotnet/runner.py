"""Core solve-field orchestration for astrometry.net."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
from astropy.io import fits

from astroeasy.config import AstrometryConfig
from astroeasy.dotnet.docker import run_against_container
from astroeasy.dotnet.local import run_against_local
from astroeasy.models import (
    Detection,
    ImageMetadata,
    MatchedStar,
    SolveResult,
    WCSResult,
    WCSStatus,
)

logger = logging.getLogger(__name__)


def _release_index_page_cache(indices_path: Path | None) -> None:
    """Release the astrometry index files' cached pages after a solve.

    ``solve-field`` memory-maps the index tiles it reads, and the kernel keeps those pages
    resident in the OS page cache after the subprocess exits. Across many solves that sweep the
    sky the resident set grows without bound toward the full index size, which presents as a slow
    memory leak for long-lived services. Advising ``POSIX_FADV_DONTNEED`` drops the clean cached
    pages; the next solve simply re-faults the tiles it needs. Only clean pages are dropped, so
    this is lossless.

    No-op on platforms without ``posix_fadvise`` (e.g. macOS, Windows) or when no index path is
    configured.

    Args:
        indices_path: Directory containing the astrometry.net index files, or ``None``.
    """
    if indices_path is None or not hasattr(os, "posix_fadvise"):
        return
    for index_file in Path(indices_path).glob("**/*.fits"):
        try:
            fd = os.open(index_file, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass
        finally:
            os.close(fd)


def _write_astrometry_cfg(config: AstrometryConfig, indices_path: str, output_path: Path) -> None:
    """Write astrometry.net configuration file.

    Args:
        config: Astrometry configuration.
        indices_path: Path to index files (may differ from config for Docker).
        output_path: Path to write the config file.
    """
    cfg_content = f"""# Astrometry.net configuration
inparallel
add_path {indices_path}
autoindex

# Minimum and maximum quad size for solver
minwidth {config.min_width_degrees}
maxwidth {config.max_width_degrees}
"""
    with open(output_path, "w") as f:
        f.write(cfg_content)


def _write_sources_fits(
    detections: list[Detection],
    output_path: Path,
    max_sources: int = 100,
) -> None:
    """Write detections to a FITS file for astrometry.net.

    Args:
        detections: List of detections to write.
        output_path: Path to write the FITS file.
        max_sources: Maximum number of sources to include.
    """
    # Sort by flux (brightest first) and limit
    sorted_detections = sorted(
        [d for d in detections if d.flux is not None],
        key=lambda d: d.flux,
        reverse=True,
    )
    # Add detections without flux at the end
    sorted_detections.extend([d for d in detections if d.flux is None])
    limited_detections = sorted_detections[:max_sources]

    # Create FITS columns
    x_col = fits.Column(
        name="X",
        format="E",
        array=np.array([d.x for d in limited_detections], dtype=np.float32),
    )
    y_col = fits.Column(
        name="Y",
        format="E",
        array=np.array([d.y for d in limited_detections], dtype=np.float32),
    )
    flux_col = fits.Column(
        name="FLUX",
        format="E",
        array=np.array(
            [d.flux if d.flux is not None else 0.0 for d in limited_detections],
            dtype=np.float32,
        ),
    )

    coldefs = fits.ColDefs([x_col, y_col, flux_col])
    hdu = fits.BinTableHDU.from_columns(coldefs)
    hdu.header["AN_FILE"] = "sources"

    primary = fits.PrimaryHDU()
    hdul = fits.HDUList([primary, hdu])
    hdul.writeto(output_path, overwrite=True)


def _read_wcs_result(wcs_path: Path) -> WCSResult:
    """Read WCS result from astrometry.net output.

    Args:
        wcs_path: Path to the .wcs FITS file.

    Returns:
        WCSResult instance.
    """
    with fits.open(wcs_path) as hdul:
        header = hdul[0].header
        return WCSResult.from_fits_header(header)


def _read_matched_stars(
    temp_path: Path,
    wcs_result: WCSResult,
    base_name: str = "sources",
) -> list[MatchedStar]:
    """Read matched stars from astrometry.net output.

    Args:
        temp_path: Directory containing astrometry.net output files.
        wcs_result: WCS solution for coordinate conversion.
        base_name: Base filename (e.g., "sources" or "image").

    Returns:
        List of MatchedStar instances.
    """
    rdls_path = temp_path / f"{base_name}.rdls"
    if not rdls_path.exists():
        return []

    matched_stars = []
    world_coords = wcs_result.to_astropy_wcs()

    with fits.open(rdls_path) as hdul:
        ra_dec_data = hdul[1].data

        # Sort by magnitude if available
        if "mag" in ra_dec_data.names:
            ra_dec_data = np.sort(ra_dec_data, order="mag")

        for line in ra_dec_data:
            ra = float(line["RA"])
            dec = float(line["DEC"])

            # Handle optional fields
            magnitude = float(line["mag"]) if "mag" in ra_dec_data.names else None
            catalog = line["ref_cat"] if "ref_cat" in ra_dec_data.names else None
            catalog_id = str(line["ref_id"]) if "ref_id" in ra_dec_data.names else None

            # Convert RA/Dec to pixel coordinates using WCS
            x, y = world_coords.all_world2pix(ra, dec, 0)

            matched_stars.append(
                MatchedStar(
                    ra=ra,
                    dec=dec,
                    magnitude=magnitude,
                    catalog=catalog,
                    catalog_id=catalog_id,
                    x=float(x),
                    y=float(y),
                )
            )

    return matched_stars


def _write_existing_wcs(wcs: WCSResult, output_path: Path) -> None:
    """Write an existing WCS to a FITS file for verification.

    Args:
        wcs: Existing WCS solution.
        output_path: Path to write the WCS FITS file.
    """
    header = fits.Header()
    for key, value in wcs.raw_header.items():
        if key and not key.startswith("COMMENT") and not key.startswith("HISTORY"):
            try:
                header[key] = value
            except (ValueError, KeyError):
                pass

    primary = fits.PrimaryHDU(header=header)
    hdul = fits.HDUList([primary])
    hdul.writeto(output_path, overwrite=True)


def _copy_output_files(temp_path: Path, output_dir: Path, base_name: str) -> None:
    """Copy astrometry.net output files to the output directory.

    Args:
        temp_path: Temporary directory containing output files.
        output_dir: Destination directory for output files.
        base_name: Base filename (e.g., "sources" or "image").
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Common astrometry.net output extensions
    extensions = [
        ".wcs",  # WCS solution
        ".rdls",  # RA/Dec list of matched stars
        ".match",  # Match file
        ".corr",  # Correspondences
        ".solved",  # Solved marker
        "-indx.xyls",  # Index stars in image coords
        ".axy",  # Augmented XY list
        "-ngc.png",  # NGC overlay (if plots enabled)
        "-objs.png",  # Objects plot (if plots enabled)
    ]

    copied_files = []
    for ext in extensions:
        src = temp_path / f"{base_name}{ext}"
        if src.exists():
            dst = output_dir / src.name
            shutil.copy2(src, dst)
            copied_files.append(src.name)

    # Also copy any other FITS files that were created
    for fits_file in temp_path.glob("*.fits"):
        if fits_file.name != f"{base_name}.fits":  # Don't copy input file
            dst = output_dir / fits_file.name
            shutil.copy2(fits_file, dst)
            copied_files.append(fits_file.name)

    if copied_files:
        logger.info(f"Copied output files to {output_dir}: {copied_files}")


def solve_field_image(
    image_path: Path,
    config: AstrometryConfig,
    existing_wcs: WCSResult | None = None,
) -> SolveResult:
    """Solve astrometry directly from a FITS image file.

    This lets astrometry.net handle source extraction, which is more robust
    than simple threshold-based extraction.

    Args:
        image_path: Path to the FITS image file.
        config: Astrometry configuration.
        existing_wcs: Optional existing WCS to verify/refine.

    Returns:
        SolveResult with WCS solution and matched stars if successful.
    """
    logger.info(f"Attempting astrometric solution on image: {image_path.name}")

    # Read image metadata
    with fits.open(image_path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and len(hdu.data.shape) == 2:
                height, width = hdu.data.shape
                break
        else:
            raise ValueError("No 2D image data found in FITS file")

    image_metadata = ImageMetadata(width=width, height=height)

    # Determine runner and indices path
    if config.docker_image:
        runner = run_against_container
        indices_path = "/usr/local/astrometry/data"
    else:
        runner = run_against_local
        indices_path = str(config.indices_path)

    temp_dir = tempfile.mkdtemp()
    try:
        temp_path = Path(temp_dir)

        # Copy image to temp directory
        image_name = "image.fits"
        shutil.copy(image_path, temp_path / image_name)

        # Write astrometry config
        _write_astrometry_cfg(config, indices_path, temp_path / "astrometry.cfg")

        # Build width/height arguments
        width_height_args = f" --width {width} --height {height}"

        tweak_order = config.tweak_order
        if tweak_order > 0:
            tweak_command = f" --tweak-order {tweak_order}"
        else:
            tweak_command = " --no-tweak"

        if existing_wcs is not None:
            # Verify existing WCS
            wcs_file = temp_path / "image.wcs"
            _write_existing_wcs(existing_wcs, wcs_file)
            solve_field_command = None
        else:
            # Build solve-field command - let astrometry.net extract sources
            solve_field_command = (
                f"timeout {config.cpulimit_seconds}s solve-field {image_name} "
                f"--config astrometry.cfg --continue"
            )
            solve_field_command += width_height_args

            # Add position hint if available (could be extracted from FITS header)
            # For now, skip hints - could be added later

            solve_field_command += " --crpix-center --overwrite --scale-units degw"
            solve_field_command += f" --scale-low {config.min_width_degrees}"
            solve_field_command += f" --scale-high {config.max_width_degrees}"
            solve_field_command += f" --continue --cpulimit {config.cpulimit_seconds}"
            solve_field_command += " --no-plots --downsample 2"
            solve_field_command += tweak_command

        # Build verification command
        verify_command = (
            f"solve-field --verify image.wcs {image_name}{tweak_command} "
            f"--tag-all --continue --no-plots"
            + width_height_args
        )

        # Run solver
        if config.docker_image:
            success = runner(
                solve_field_command,
                temp_path,
                config.indices_path,
                config.docker_image,
                verify_command,
                config.output_dir,
            )
        else:
            success = runner(solve_field_command, temp_path, verify_command)

        if success:
            wcs_result = _read_wcs_result(temp_path / "image.wcs")
            matched_stars = _read_matched_stars(temp_path, wcs_result, base_name="image")
            status = WCSStatus.SUCCESS
        else:
            wcs_result = None
            matched_stars = []
            status = WCSStatus.FAILED

        # Copy output files if output_dir is specified
        if config.output_dir:
            _copy_output_files(temp_path, config.output_dir, base_name="image")

        return SolveResult(
            success=success,
            status=status,
            wcs=wcs_result,
            matched_stars=matched_stars,
            detections=[],  # No pre-extracted detections in image mode
            image_metadata=image_metadata,
        )

    finally:
        # Release the index page cache faulted in by solve-field so repeated solves do not
        # accumulate the whole index in memory (see _release_index_page_cache). Opt out via
        # config.release_index_page_cache.
        if config.release_index_page_cache:
            _release_index_page_cache(config.indices_path)
        # Cleanup
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to cleanup temporary directory {temp_dir}: {e}")


def solve_field(
    detections: list[Detection],
    image_metadata: ImageMetadata,
    config: AstrometryConfig,
    existing_wcs: WCSResult | None = None,
) -> SolveResult:
    """Solve astrometry for a set of detections.

    Args:
        detections: List of detected sources with x, y coordinates.
        image_metadata: Metadata about the image (width, height, optional hints).
        config: Astrometry configuration.
        existing_wcs: Optional existing WCS to verify/refine.

    Returns:
        SolveResult with WCS solution and matched stars if successful.
    """
    sources_to_use = min(len(detections), config.max_sources)
    logger.info(
        f"Attempting astrometric solution using {sources_to_use} of {len(detections)} sources"
    )

    # Determine runner and indices path
    if config.docker_image:
        runner = run_against_container
        indices_path = "/usr/local/astrometry/data"
    else:
        runner = run_against_local
        indices_path = str(config.indices_path)

    temp_dir = tempfile.mkdtemp()
    try:
        temp_path = Path(temp_dir)

        # Write sources file
        sources_file = temp_path / "sources.fits"
        _write_sources_fits(detections, sources_file, config.max_sources)

        # Write astrometry config
        _write_astrometry_cfg(config, indices_path, temp_path / "astrometry.cfg")

        # Build width/height arguments
        width_height_args = f" --width {image_metadata.width} --height {image_metadata.height}"

        tweak_order = config.tweak_order
        if tweak_order > 0:
            tweak_command = f" --tweak-order {tweak_order}"
        else:
            tweak_command = " --no-tweak"

        if existing_wcs is not None:
            # Verify existing WCS
            wcs_file = temp_path / "sources.wcs"
            _write_existing_wcs(existing_wcs, wcs_file)
            solve_field_command = None
        else:
            # Build solve-field command
            solve_field_command = (
                f"timeout {config.cpulimit_seconds}s solve-field sources.fits --config astrometry.cfg --continue"
                " --x-column X --y-column Y"
            )
            solve_field_command += width_height_args

            # Add position hint if available
            if image_metadata.boresight_ra is not None and image_metadata.boresight_dec is not None:
                solve_field_command += (
                    f" --ra {image_metadata.boresight_ra}"
                    f" --dec {image_metadata.boresight_dec}"
                    f" --radius {config.search_radius}"
                )

            solve_field_command += " --crpix-center --overwrite --scale-units degw"
            solve_field_command += f" --scale-low {config.min_width_degrees}"
            solve_field_command += f" --scale-high {config.max_width_degrees}"
            if config.odds_to_solve is not None:
                solve_field_command += f" --odds-to-solve {config.odds_to_solve}"
            solve_field_command += (
                f" --continue --cpulimit {config.cpulimit_seconds} --sort-column FLUX"
            )
            solve_field_command += " --no-plots"
            solve_field_command += tweak_command

        # Build verification command
        verify_command = (
            f"solve-field --verify sources.wcs sources.fits{tweak_command} --tag-all --continue --no-plots"
            + width_height_args
        )

        # Run solver
        if config.docker_image:
            success = runner(
                solve_field_command,
                temp_path,
                config.indices_path,
                config.docker_image,
                verify_command,
                config.output_dir,
            )
        else:
            success = runner(solve_field_command, temp_path, verify_command)

        if success:
            wcs_result = _read_wcs_result(temp_path / "sources.wcs")
            matched_stars = _read_matched_stars(temp_path, wcs_result)
            status = WCSStatus.SUCCESS
        else:
            wcs_result = None
            matched_stars = []
            status = WCSStatus.FAILED

        # Copy output files if output_dir is specified
        if config.output_dir:
            _copy_output_files(temp_path, config.output_dir, base_name="sources")

        return SolveResult(
            success=success,
            status=status,
            wcs=wcs_result,
            matched_stars=matched_stars,
            detections=detections,
            image_metadata=image_metadata,
        )

    finally:
        # Release the index page cache faulted in by solve-field so repeated solves do not
        # accumulate the whole index in memory (see _release_index_page_cache). Opt out via
        # config.release_index_page_cache.
        if config.release_index_page_cache:
            _release_index_page_cache(config.indices_path)
        # Cleanup
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to cleanup temporary directory {temp_dir}: {e}")
