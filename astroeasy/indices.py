"""Astrometry.net index file management."""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from tqdm import tqdm

from astroeasy.constants import (
    ASTROMETRY_4100_EXPECTED_STRUCTURE,
    ASTROMETRY_4200_EXPECTED_STRUCTURE,
    ASTROMETRY_5200_EXPECTED_STRUCTURE,
    ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE,
    ASTROMETRY_INDICES_URL_4100,
    ASTROMETRY_INDICES_URL_4200,
    ASTROMETRY_INDICES_URL_5200,
    ASTROMETRY_INDICES_URL_5200_LITE,
    INDEX_SCALE_RANGES,
    AstrometryIndexSeries,
)

logger = logging.getLogger(__name__)


def human_readable_size(size_bytes: int) -> str:
    """Convert size in bytes to human-readable format with appropriate units."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_index = 0
    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def get_fits_files(base_url: str) -> list[str]:
    """Get list of FITS files from a URL.

    Args:
        base_url: URL to scan for FITS files.

    Returns:
        List of full URLs to FITS files.
    """
    with urlopen(base_url) as response:
        html = response.read().decode("utf-8")

    # Exclude files matching index-##m#-*.fits pattern (multi-scale)
    fits_files = [
        base_url + filename
        for filename in re.findall(r'href="([^"]+\.fits)"', html)
        if not re.match(r"index-\d+m\d+-.*\.fits", filename)
    ]

    return fits_files


def scales_for_fov(fov_degrees: float) -> list[int]:
    """Compute needed index scale numbers for a given field of view.

    Uses the astrometry.net recommendation that index quads should be
    10%-100% of the image FOV.

    Args:
        fov_degrees: Field of view in degrees.

    Returns:
        Sorted list of scale numbers (0-19) needed for this FOV.
    """
    fov_arcmin = fov_degrees * 60.0
    quad_min = 0.1 * fov_arcmin  # 10% of FOV
    quad_max = fov_arcmin  # 100% of FOV

    scales = []
    for scale, (lo, hi) in INDEX_SCALE_RANGES.items():
        # Include if scale range overlaps with [quad_min, quad_max]
        if hi > quad_min and lo < quad_max:
            scales.append(scale)

    return sorted(scales)


def filter_structure_by_scales(
    expected_structure: dict[str, int],
    scales: list[int],
) -> dict[str, int]:
    """Filter an expected_structure dict to only include files matching given scales.

    Args:
        expected_structure: Filename -> size mapping.
        scales: List of scale numbers to keep.

    Returns:
        Filtered filename -> size mapping.
    """
    scale_set = set(scales)
    filtered = {}
    for filename, size in expected_structure.items():
        m = re.match(r"index-(\d{2})(\d{2})(?:-\d+)?\.fits", filename)
        if m:
            file_scale = int(m.group(2))
            if file_scale in scale_set:
                filtered[filename] = size
        else:
            # Keep files that don't match the pattern (shouldn't happen)
            filtered[filename] = size
    return filtered


def download_fits_files(
    base_url: str,
    output_dir: str | Path | None = None,
    max_workers: int = 5,
    filenames: set[str] | None = None,
) -> None:
    """Download .fits files, skipping existing files of the same size.

    Args:
        base_url: The URL to download .fits files from.
        output_dir: Directory to save files to. Defaults to current directory.
        max_workers: Number of concurrent downloads. Defaults to 5.
        filenames: If set, only download files whose basename is in this set.
    """
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    fits_files = get_fits_files(base_url)

    if filenames is not None:
        fits_files = [url for url in fits_files if url.split("/")[-1] in filenames]

    if not fits_files:
        logger.warning("No .fits files found!")
        return

    logger.info(f"Found {len(fits_files)} .fits files")

    def download_file(url):
        try:
            filename = url.split("/")[-1]
            if output_dir:
                filename = os.path.join(output_dir, filename)

            # Check if file exists
            if os.path.exists(filename):
                # Get remote file size
                req = Request(url, method="HEAD")
                with urlopen(req) as response:
                    remote_size = int(response.headers["Content-Length"])

                # Get local file size
                local_size = os.path.getsize(filename)

                if remote_size == local_size:
                    return
                else:
                    tqdm.write(f"Size mismatch for {filename}, downloading again...")
                    tqdm.write(f"Remote: {remote_size} bytes, Local: {local_size} bytes")
            else:
                tqdm.write(f"Downloading new file {filename}...")

            # Download with progress bar
            req = Request(url, method="HEAD")
            with urlopen(req) as response:
                file_size = int(response.headers["Content-Length"])

            with urlopen(url) as response:
                with open(filename, "wb") as f:
                    with tqdm(
                        total=file_size, unit="B", unit_scale=True, desc=filename, leave=False
                    ) as pbar:
                        while True:
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                            pbar.update(len(chunk))

            tqdm.write(f"Successfully downloaded {filename}")
        except Exception as e:
            tqdm.write(f"Error downloading {url}: {e}")

    # Overall progress bar for all files
    with tqdm(total=len(fits_files), desc="Total progress", unit="file") as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for url in fits_files:
                future = executor.submit(download_file, url)
                future.add_done_callback(lambda p: pbar.update())
                futures.append(future)


def extract_expected_structure(base_url: str) -> dict[str, int] | None:
    """Extract expected file sizes from remote server.

    Args:
        base_url: URL to scan for FITS files.

    Returns:
        Dictionary mapping filename to expected size in bytes.
    """
    fits_files = get_fits_files(base_url)

    if not fits_files:
        logger.warning("No .fits files found!")
        return None

    logger.info(f"Found {len(fits_files)} .fits files")

    results_dict = {}

    for url in tqdm(fits_files, desc="Extracting expected filesizes", ascii=True):
        filename = url.split("/")[-1]

        req = Request(url, method="HEAD")
        with urlopen(req) as response:
            remote_size = int(response.headers["Content-Length"])
            results_dict[filename] = remote_size

    return results_dict


def examine_by_path_and_structure(
    series: AstrometryIndexSeries | str,
    indices_path: str | Path,
    expected_structure: dict[str, int],
) -> bool:
    """Validate index files against expected structure.

    Args:
        series: Index series name for logging.
        indices_path: Path to the indices directory.
        expected_structure: Expected filename -> size mapping.

    Returns:
        True if all files are present and valid, False otherwise.
    """
    missing_indices = []
    size_mismatch_indices = []

    if series == AstrometryIndexSeries.SERIES_CUSTOM:
        logger.warning(
            f"[{series}] Astrometry indices are custom, skipping validation. "
            "Please consider adding this to the codebase."
        )
        return True

    for filename, expected_size in expected_structure.items():
        filepath = Path(indices_path) / filename
        if filepath.exists():
            local_size = os.path.getsize(filepath)

            if expected_size != local_size:
                size_mismatch_indices.append(filename)
        else:
            missing_indices.append(filename)

    complete_set = len(size_mismatch_indices) + len(missing_indices) == 0

    if complete_set:
        logger.info(
            f"[{series}] Astrometry indices [{series}] are complete and valid [{indices_path}]"
        )
        return True

    if len(size_mismatch_indices) > 0:
        logger.warning(
            f"[{series}] Astrometry indices size mismatch for {', '.join(size_mismatch_indices)}"
        )
    if len(missing_indices) > 0:
        logger.warning(f"[{series}] Astrometry indices missing: {', '.join(missing_indices)}")

    logger.warning(f"[{series}] Astrometry indices are incomplete")
    logger.warning(
        f"[{series}] fix: astroeasy indices download --series {series} --output {indices_path}"
    )

    return False


def get_expected_structure(
    series: AstrometryIndexSeries,
) -> tuple[list[str], dict[str, int]]:
    """Get download URLs and expected file structure for an index series.

    Args:
        series: The index series to get structure for.

    Returns:
        Tuple of (list of base URLs, expected filename -> size mapping).
    """
    if series == AstrometryIndexSeries.SERIES_5200:
        base_urls = [ASTROMETRY_INDICES_URL_5200]
        expected_structure = ASTROMETRY_5200_EXPECTED_STRUCTURE
    elif series == AstrometryIndexSeries.SERIES_5200_LITE:
        base_urls = [ASTROMETRY_INDICES_URL_5200_LITE]
        expected_structure = ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE
    elif series == AstrometryIndexSeries.SERIES_4100:
        base_urls = [ASTROMETRY_INDICES_URL_4100]
        expected_structure = ASTROMETRY_4100_EXPECTED_STRUCTURE
    elif series == AstrometryIndexSeries.SERIES_4200:
        base_urls = [ASTROMETRY_INDICES_URL_4200]
        expected_structure = ASTROMETRY_4200_EXPECTED_STRUCTURE
    elif series == AstrometryIndexSeries.SERIES_5200_LITE_4100:
        base_urls = [ASTROMETRY_INDICES_URL_5200_LITE, ASTROMETRY_INDICES_URL_4100]
        expected_structure = (
            ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE | ASTROMETRY_4100_EXPECTED_STRUCTURE
        )
    elif series == AstrometryIndexSeries.SERIES_CUSTOM:
        base_urls = []
        expected_structure = {}
    else:
        raise AttributeError(f"Unknown series {series}")

    return base_urls, expected_structure


def examine_indices(
    indices_path: str | Path,
    series: AstrometryIndexSeries = AstrometryIndexSeries.SERIES_5200_LITE,
    fov_degrees: float | None = None,
) -> bool:
    """Examine indices at the given path for completeness.

    Args:
        indices_path: Path to the indices directory.
        series: Which index series to validate against.
        fov_degrees: If set, only check files for scales matching this FOV.

    Returns:
        True if indices are complete and valid.
    """
    _, expected_structure = get_expected_structure(series)
    if fov_degrees is not None:
        scales = scales_for_fov(fov_degrees)
        expected_structure = filter_structure_by_scales(expected_structure, scales)
    return examine_by_path_and_structure(series, indices_path, expected_structure)


def download_indices(
    output_path: str | Path,
    series: AstrometryIndexSeries = AstrometryIndexSeries.SERIES_5200_LITE,
    max_workers: int = 5,
    fov_degrees: float | None = None,
) -> None:
    """Download index files for the specified series.

    Args:
        output_path: Directory to download indices to.
        series: Which index series to download.
        max_workers: Number of concurrent downloads.
        fov_degrees: If set, only download files for scales matching this FOV.
    """
    base_urls, expected_structure = get_expected_structure(series)

    if not base_urls:
        raise ValueError(f"No download URLs available for series {series}")

    needed_filenames = None
    if fov_degrees is not None:
        scales = scales_for_fov(fov_degrees)
        filtered = filter_structure_by_scales(expected_structure, scales)
        needed_filenames = set(filtered.keys())

    for base_url in base_urls:
        download_fits_files(
            base_url,
            output_dir=output_path,
            max_workers=max_workers,
            filenames=needed_filenames,
        )


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Astrometry indices management")
    parser.add_argument(
        "action",
        choices=["download", "examine", "map_expected", "supported"],
        help="Action to perform",
    )
    parser.add_argument(
        "--series",
        type=AstrometryIndexSeries,
        choices=list(AstrometryIndexSeries),
        required=False,
        default=AstrometryIndexSeries.SERIES_5200_LITE,
        help="Index series to use",
    )
    parser.add_argument("--index_path", required=False, help="Path to the indices directory")
    parser.add_argument("--workers", type=int, default=5, help="Number of concurrent downloads")

    args = parser.parse_args()

    base_urls, expected_structure = get_expected_structure(args.series)

    if args.action == "download":
        for base_url in base_urls:
            download_fits_files(base_url, output_dir=args.index_path, max_workers=args.workers)

    elif args.action == "examine":
        examine_by_path_and_structure(args.series, args.index_path, expected_structure)

    elif args.action == "supported":
        print(f"Supported indices: {', '.join(str(s) for s in AstrometryIndexSeries)}")

    elif args.action == "map_expected":
        expected = {}

        for series, url in zip(
            ["4100", "4200", "5200", "5200_LITE"],
            [
                ASTROMETRY_INDICES_URL_4100,
                ASTROMETRY_INDICES_URL_4200,
                ASTROMETRY_INDICES_URL_5200,
                ASTROMETRY_INDICES_URL_5200_LITE,
            ],
            strict=False,
        ):
            if series == str(args.series):
                expected[series] = extract_expected_structure(url)

        print(json.dumps(expected, indent=4))
