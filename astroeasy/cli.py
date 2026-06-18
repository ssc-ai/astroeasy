"""Command-line interface for astroeasy."""

import argparse
import csv
import logging
import sys
from pathlib import Path

from astroeasy.catalog.gaia import get_field_bounds_from_wcs, query_gaia_field
from astroeasy.config import AstrometryConfig
from astroeasy.constants import AstrometryIndexSeries
from astroeasy.indices import download_indices, examine_indices
from astroeasy.models import Detection, ImageMetadata
from astroeasy.plotting import plot_solved_field
from astroeasy.runner import (
    solve_field,
    solve_field_aggressive,
    solve_field_image,
    test_install,
)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def cmd_indices_download(args: argparse.Namespace) -> int:
    """Handle 'indices download' command."""
    from astroeasy.indices import (
        filter_structure_by_scales,
        get_expected_structure,
        human_readable_size,
        scales_for_fov,
    )

    series = AstrometryIndexSeries(args.series)
    output_path = Path(args.output)
    fov = getattr(args, "fov", None)

    if fov is not None:
        # FOV-filtered subsets get an automatic subdirectory (e.g. 4200_2p0deg)
        # so they can't be mistaken for (or mixed into) a full series download.
        fov_tag = str(fov).replace(".", "p")
        output_path = output_path / f"{series}_{fov_tag}deg"
        scales = scales_for_fov(fov)
        _, expected_structure = get_expected_structure(series)
        filtered = filter_structure_by_scales(expected_structure, scales)
        total_size = sum(filtered.values())
        print(
            f"FOV {fov}° -> scales {scales} -> "
            f"{len(filtered)} files ({human_readable_size(total_size)})"
        )

    print(f"Downloading {series} indices to {output_path}")
    download_indices(output_path, series, max_workers=args.workers, fov_degrees=fov)
    print("Download complete.")
    return 0


def cmd_indices_examine(args: argparse.Namespace) -> int:
    """Handle 'indices examine' command."""
    series = AstrometryIndexSeries(args.series)
    path = Path(args.path)
    fov = getattr(args, "fov", None)

    if examine_indices(path, series, fov_degrees=fov):
        print(f"Indices at {path} are complete and valid for {series}.")
        return 0
    else:
        print(f"Indices at {path} are incomplete for {series}.")
        return 1


def cmd_indices_recommend(args: argparse.Namespace) -> int:
    """Handle 'indices recommend' command."""
    from astroeasy.constants import INDEX_SCALE_RANGES
    from astroeasy.indices import (
        filter_structure_by_scales,
        get_expected_structure,
        human_readable_size,
        scales_for_fov,
    )

    fov = args.fov
    scales = scales_for_fov(fov)
    fov_arcmin = fov * 60.0

    print(f"FOV: {fov}° ({fov_arcmin:.1f} arcmin)")
    print(f"Recommended quad range: {0.1 * fov_arcmin:.1f}' - {fov_arcmin:.1f}' (10%-100% of FOV)")
    print(f"Matching index scales: {scales}")
    print()

    # Show scale details
    print("Scale details:")
    for s in scales:
        lo, hi = INDEX_SCALE_RANGES[s]
        print(f"  scale {s:2d}: {lo:.1f}' - {hi:.1f}'")
    print()

    # Show per-series breakdown
    skip = {AstrometryIndexSeries.SERIES_CUSTOM}
    print(f"{'Series':<20s} {'Files':>6s} {'Size':>10s}")
    print("-" * 38)
    for series in AstrometryIndexSeries:
        if series in skip:
            continue
        _, expected = get_expected_structure(series)
        filtered = filter_structure_by_scales(expected, scales)
        total = sum(filtered.values())
        print(f"{series.value:<20s} {len(filtered):>6d} {human_readable_size(total):>10s}")

    print()
    print("Example download command:")
    print(f"  astroeasy indices download --series 5200_LITE --output /data/indices --fov {fov}")

    # Generate config file if requested
    config_path = getattr(args, "config", None)
    if config_path:
        import yaml

        series = AstrometryIndexSeries(args.series)
        indices_path = args.indices_path or "/data/indices"
        # Use FOV to set width constraints with some margin
        min_width = fov * 0.5
        max_width = fov * 2.0
        config_data = {
            "indices_path": indices_path,
            "indices_series": str(series),
            "min_width_degrees": round(min_width, 3),
            "max_width_degrees": round(max_width, 3),
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f, default_flow_style=False)
        print()
        print(f"Config written to {config_path}")

    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    """Handle 'solve' command."""
    # Load config
    if args.config:
        config = AstrometryConfig.from_yaml(args.config)
    else:
        if not args.indices_path:
            print("Error: --indices-path is required when not using --config")
            return 1
        config = AstrometryConfig(
            indices_path=Path(args.indices_path),
            docker_image=args.docker_image,
            min_width_degrees=args.scale_low,
            max_width_degrees=args.scale_high,
            cpulimit_seconds=args.cpulimit,
        )

    # Override output_dir from CLI if provided
    if args.output_dir:
        config.output_dir = Path(args.output_dir)

    # Override docker_image from CLI if provided (allows overriding config file)
    if args.docker_image:
        config.docker_image = args.docker_image

    # Image mode: pass image directly to astrometry.net for source extraction
    if args.image:
        print(f"Solving image: {args.image}")
        print("(astrometry.net will extract sources)")

        if args.aggressive:
            print("Note: --aggressive is ignored in image mode (astrometry.net extracts sources)")

        result = solve_field_image(args.image, config)

        if result.success:
            print("Solved!")
            print(f"  Center RA:  {result.wcs.center_ra:.6f} deg")
            print(f"  Center Dec: {result.wcs.center_dec:.6f} deg")
            print(f"  Pixel scale: {result.wcs.pixel_scale:.3f} arcsec/pix")
            print(f"  Matched stars: {len(result.matched_stars)}")
            return 0
        else:
            print(f"Failed to solve: {result.status.value}")
            return 1

    # XYlist mode: use pre-extracted sources
    elif args.xylist:
        detections = _load_detections_from_file(args.xylist)
        if not detections:
            print(f"Error: No detections loaded from {args.xylist}")
            return 1
        if not args.width or not args.height:
            print("Error: --width and --height are required with --xylist")
            return 1
        metadata = ImageMetadata(
            width=args.width,
            height=args.height,
            boresight_ra=args.ra,
            boresight_dec=args.dec,
        )

        print(f"Loaded {len(detections)} detections")

        if args.aggressive:
            print("Using aggressive mode (trying multiple max_sources values)...")
            agg_result = solve_field_aggressive(detections, metadata, config)
            result = agg_result.result

            # Print attempt history
            print("Attempts:")
            for max_sources, success in agg_result.attempts:
                status = "success" if success else "failed"
                print(f"  max_sources={max_sources}: {status}")

            if result.success:
                print(f"Solved with max_sources={agg_result.successful_max_sources}!")
                print(f"  Center RA:  {result.wcs.center_ra:.6f} deg")
                print(f"  Center Dec: {result.wcs.center_dec:.6f} deg")
                print(f"  Pixel scale: {result.wcs.pixel_scale:.3f} arcsec/pix")
                print(f"  Matched stars: {len(result.matched_stars)}")
                return 0
            else:
                print(f"Failed to solve with all max_sources values: {result.status.value}")
                return 1
        else:
            result = solve_field(detections, metadata, config)

            if result.success:
                print("Solved!")
                print(f"  Center RA:  {result.wcs.center_ra:.6f} deg")
                print(f"  Center Dec: {result.wcs.center_dec:.6f} deg")
                print(f"  Pixel scale: {result.wcs.pixel_scale:.3f} arcsec/pix")
                print(f"  Matched stars: {len(result.matched_stars)}")
                return 0
            else:
                print(f"Failed to solve: {result.status.value}")
                return 1

    else:
        print("Error: Either --image or --xylist is required")
        return 1


def cmd_test_install(args: argparse.Namespace) -> int:
    """Handle 'test-install' command."""
    docker_image = args.docker if args.docker else None

    if docker_image:
        print(f"Testing Docker installation: {docker_image}")
    else:
        print("Testing local installation")

    if test_install(docker_image):
        print("Installation test passed!")
        return 0
    else:
        print("Installation test failed.")
        return 1


def infer_index_series(indices_path: Path) -> AstrometryIndexSeries:
    """Infer the index series from files in a directory.

    Examines the FITS files in the directory and determines which
    astrometry.net index series they belong to.

    Args:
        indices_path: Path to directory containing index files.

    Returns:
        The inferred AstrometryIndexSeries, or CUSTOM if unrecognized.
    """
    if not indices_path.exists():
        return AstrometryIndexSeries.SERIES_CUSTOM

    fits_files = list(indices_path.glob("index-*.fits"))
    if not fits_files:
        return AstrometryIndexSeries.SERIES_CUSTOM

    filenames = {f.name for f in fits_files}

    # Check for 4100 series (index-41XX.fits without healpix)
    has_4100 = any(f.startswith("index-41") and "-" not in f[9:] for f in filenames)

    # Check for 4200 series (index-42XX-XX.fits)
    has_4200 = any(f.startswith("index-42") for f in filenames)

    # Check for 5200 series (index-52XX-XX.fits)
    has_5200 = any(f.startswith("index-52") for f in filenames)

    # If we have both 4100 and 5200, it's likely 5200_LITE_4100
    if has_4100 and has_5200:
        return AstrometryIndexSeries.SERIES_5200_LITE_4100

    if has_4100:
        return AstrometryIndexSeries.SERIES_4100

    if has_4200:
        return AstrometryIndexSeries.SERIES_4200

    if has_5200:
        # Distinguish between 5200 and 5200_LITE by file sizes
        sample_file = next((f for f in fits_files if f.name.startswith("index-5200-00")), None)
        if sample_file:
            size = sample_file.stat().st_size
            # 5200_LITE ~331MB, 5200 ~628MB for index-5200-00.fits
            if size < 400_000_000:
                return AstrometryIndexSeries.SERIES_5200_LITE
            else:
                return AstrometryIndexSeries.SERIES_5200
        return AstrometryIndexSeries.SERIES_5200_LITE  # Default to LITE

    return AstrometryIndexSeries.SERIES_CUSTOM


def _prompt_with_default(prompt: str, default: str) -> str:
    """Prompt user for input with a default value."""
    result = input(f"{prompt} [{default}]: ").strip()
    return result if result else default


def _prompt_choice(prompt: str, choices: list[str], default: str) -> str:
    """Prompt user to choose from a list of options."""
    print(f"\n{prompt}")
    for i, choice in enumerate(choices, 1):
        marker = " (default)" if choice == default else ""
        print(f"  {i}. {choice}{marker}")

    while True:
        result = input(f"Enter choice [1-{len(choices)}] or value [{default}]: ").strip()
        if not result:
            return default
        if result.isdigit() and 1 <= int(result) <= len(choices):
            return choices[int(result) - 1]
        if result in choices:
            return result
        print(f"Invalid choice. Please enter 1-{len(choices)} or a valid value.")


def cmd_build_config(args: argparse.Namespace) -> int:
    """Handle 'build-config' command."""
    output_path = Path(args.output) if args.output else Path("astroeasy.yaml")

    # Check if we're in interactive mode (no indices-path specified)
    interactive = args.indices_path is None and not args.non_interactive

    if interactive:
        print("Astroeasy Configuration Builder")
        print("=" * 40)
        print("\nThis will create a YAML configuration file for astroeasy.")
        print("Press Enter to accept default values.\n")

        # Indices path (required)
        while True:
            indices_path_str = input("Path to index files (required): ").strip()
            if indices_path_str:
                indices_path = Path(indices_path_str).expanduser().resolve()
                if indices_path.exists():
                    break
                else:
                    create = input(
                        f"Directory {indices_path} doesn't exist. Continue anyway? [y/N]: "
                    )
                    if create.lower() == "y":
                        break
            else:
                print("Indices path is required.")

        # Infer series from path
        inferred_series = infer_index_series(indices_path)
        series_choices = [s.value for s in AstrometryIndexSeries]
        indices_series = _prompt_choice(
            "Index series:",
            series_choices,
            inferred_series.value,
        )

        # Docker image
        docker_image = _prompt_with_default(
            "Docker image (leave empty for local installation)",
            "",
        )
        docker_image = docker_image if docker_image else None

        # Scale bounds
        scale_low = float(_prompt_with_default("Minimum field width in degrees", "0.1"))
        scale_high = float(_prompt_with_default("Maximum field width in degrees", "10.0"))

        # CPU limit
        cpulimit = int(_prompt_with_default("CPU time limit in seconds", "30"))

        # Max sources
        max_sources = int(_prompt_with_default("Maximum sources for solving", "100"))

        # Output file
        output_path_str = _prompt_with_default("Output config file", str(output_path))
        output_path = Path(output_path_str)

    else:
        # Non-interactive mode - use CLI args
        if not args.indices_path:
            print("Error: --indices-path is required in non-interactive mode")
            return 1

        indices_path = Path(args.indices_path).expanduser().resolve()

        # Infer or use specified series
        if args.indices_series:
            indices_series = args.indices_series
        else:
            inferred = infer_index_series(indices_path)
            indices_series = inferred.value
            print(f"Inferred index series: {indices_series}")

        docker_image = args.docker_image
        scale_low = args.scale_low
        scale_high = args.scale_high
        cpulimit = args.cpulimit
        max_sources = args.max_sources

    # Create the config
    config = AstrometryConfig(
        indices_path=indices_path,
        indices_series=AstrometryIndexSeries(indices_series),
        docker_image=docker_image,
        min_width_degrees=scale_low,
        max_width_degrees=scale_high,
        cpulimit_seconds=cpulimit,
        max_sources=max_sources,
    )

    # Write to file
    config.to_yaml(output_path)
    print(f"\nConfiguration saved to: {output_path}")

    # Show summary
    print("\nConfiguration summary:")
    print(f"  indices_path: {config.indices_path}")
    print(f"  indices_series: {config.indices_series}")
    print(f"  docker_image: {config.docker_image or '(local installation)'}")
    print(f"  scale_range: {config.min_width_degrees} - {config.max_width_degrees} degrees")
    print(f"  cpulimit: {config.cpulimit_seconds}s")
    print(f"  max_sources: {config.max_sources}")

    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    """Handle 'plot' command."""
    from astropy.io import fits
    from astropy.wcs import WCS

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Error: Image file not found: {image_path}")
        return 1

    # Load WCS
    wcs = None
    if args.wcs:
        wcs_path = Path(args.wcs)
        if not wcs_path.exists():
            print(f"Error: WCS file not found: {wcs_path}")
            return 1
        with fits.open(wcs_path) as hdul:
            wcs = WCS(hdul[0].header)
    else:
        # Try to get WCS from image header
        with fits.open(image_path) as hdul:
            for hdu in hdul:
                try:
                    test_wcs = WCS(hdu.header)
                    if test_wcs.has_celestial:
                        wcs = test_wcs
                        break
                except Exception:
                    continue

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = image_path.with_suffix(".png")

    # Load matched stars from .rdls file if specified
    matched_stars = None
    if args.matched:
        matched_path = Path(args.matched)
        if matched_path.exists():
            from astroeasy.models import MatchedStar

            with fits.open(matched_path) as hdul:
                if len(hdul) > 1 and hdul[1].data is not None:
                    matched_stars = []
                    data = hdul[1].data
                    for row in data:
                        ra = float(row["RA"]) if "RA" in data.names else None
                        dec = float(row["DEC"]) if "DEC" in data.names else None
                        if ra is not None and dec is not None:
                            matched_stars.append(
                                MatchedStar(ra=ra, dec=dec, x=None, y=None)
                            )
                    print(f"Loaded {len(matched_stars)} matched stars from {matched_path}")

    # Query Gaia catalog if requested
    catalog_stars = None
    if args.gaia and wcs is not None:
        print("Querying Gaia catalog...")
        try:
            # Get image dimensions
            with fits.open(image_path) as hdul:
                for hdu in hdul:
                    if hdu.data is not None and len(hdu.data.shape) == 2:
                        height, width = hdu.data.shape
                        break
                else:
                    print("Warning: Could not determine image dimensions for Gaia query")
                    width, height = 1024, 1024

            min_ra, max_ra, min_dec, max_dec = get_field_bounds_from_wcs(wcs, width, height)

            # Expand bounds slightly to ensure edge coverage
            ra_margin = (max_ra - min_ra) * 0.1 if max_ra > min_ra else 0.5
            dec_margin = (max_dec - min_dec) * 0.1

            catalog_stars = query_gaia_field(
                min_ra=min_ra - ra_margin,
                max_ra=max_ra + ra_margin,
                min_dec=min_dec - dec_margin,
                max_dec=max_dec + dec_margin,
                faint_limit=args.gaia_limit,
                max_stars=args.gaia_max,
            )
            print(f"Queried {len(catalog_stars)} Gaia stars (G < {args.gaia_limit})")
        except ImportError:
            print("Warning: astroquery not installed, skipping Gaia overlay")
            print("Install with: pip install astroquery")
        except Exception as e:
            print(f"Warning: Gaia query failed: {e}")

    # Generate plot
    print(f"Generating plot: {output_path}")

    plot_solved_field(
        image_path=image_path,
        wcs=wcs,
        output_path=output_path,
        matched_stars=matched_stars,
        catalog_stars=catalog_stars,
        show_grid=not args.no_grid,
        show_matched=matched_stars is not None,
        show_catalog=catalog_stars is not None,
        catalog_limit=args.gaia_max,
        dpi=args.dpi,
        title=args.title,
    )

    print(f"Plot saved to: {output_path}")
    return 0


def cmd_examples(args: argparse.Namespace) -> int:
    """Handle 'examples' command."""
    examples_text = """
ASTROEASY USAGE EXAMPLES
========================

1. DOCKER CLI USAGE (without Python)
------------------------------------

Build the Docker image:
  docker build -t astrometry-cli astroeasy/dotnet/

Run solve-field directly in Docker:
  docker run --rm -v /path/to/indices:/indices -v /path/to/data:/data \\
    astrometry-cli solve-field --config /indices/astrometry.cfg \\
    --xylist /data/sources.fits --width 1024 --height 1024

2. SOLVING FROM DETECTED STARS
------------------------------

From CSV file (with header):
  python -m astroeasy.cli solve \\
    --xylist sources.csv \\
    --width 1024 --height 1024 \\
    --indices-path /path/to/indices \\
    --docker-image astrometry-cli

From tab-separated file (no header, x y flux columns):
  python -m astroeasy.cli solve \\
    --xylist detections.txt \\
    --width 1024 --height 1024 \\
    --indices-path /path/to/indices \\
    --docker-image astrometry-cli

With coordinate hints (faster solving):
  python -m astroeasy.cli solve \\
    --xylist sources.csv \\
    --width 4096 --height 4096 \\
    --ra 180.0 --dec 45.0 \\
    --indices-path /path/to/indices \\
    --docker-image astrometry-cli

3. SOLVING FROM FITS IMAGES
---------------------------

  python -m astroeasy.cli solve \\
    --image observation.fits \\
    --indices-path /path/to/indices \\
    --docker-image astrometry-cli

4. AGGRESSIVE MODE
------------------

Try multiple max_sources values automatically:
  python -m astroeasy.cli solve \\
    --xylist sources.csv \\
    --width 1024 --height 1024 \\
    --indices-path /path/to/indices \\
    --docker-image astrometry-cli \\
    --aggressive

5. PYTHON API USAGE
-------------------

Basic solve:
  from astroeasy import (
      AstrometryConfig, Detection, ImageMetadata, solve_field
  )

  config = AstrometryConfig(
      indices_path="/data/indices/5200-LITE",
      docker_image="astrometry-cli",
  )
  detections = [Detection(x=100, y=200, flux=1000), ...]
  metadata = ImageMetadata(width=4096, height=4096)

  result = solve_field(detections, metadata, config)
  if result.success:
      print(f"Center: {result.wcs.center_ra}, {result.wcs.center_dec}")

Aggressive solve:
  from astroeasy import (
      AstrometryConfig, Detection, ImageMetadata, solve_field_aggressive
  )

  result = solve_field_aggressive(detections, metadata, config)
  if result.result.success:
      print(f"Solved with {result.successful_max_sources} sources")

6. INDEX MANAGEMENT
-------------------

Download indices:
  python -m astroeasy.cli indices download \\
    --series 5200_LITE --output /path/to/indices

Verify indices:
  python -m astroeasy.cli indices examine \\
    --series 5200_LITE --path /path/to/indices

7. CONFIGURATION
----------------

Interactive config builder (prompts for all values):
  python -m astroeasy.cli build-config

Non-interactive config builder (auto-detects index series):
  python -m astroeasy.cli build-config \\
    --indices-path /path/to/indices \\
    --docker-image astrometry-cli \\
    --non-interactive \\
    -o my-config.yaml

Use config file with solve:
  python -m astroeasy.cli solve \\
    --config my-config.yaml \\
    --xylist sources.csv \\
    --width 1024 --height 1024

8. TESTING INSTALLATION
-----------------------

Test Docker installation:
  python -m astroeasy.cli test-install --docker astrometry-cli

Test local installation:
  python -m astroeasy.cli test-install --local

9. PLOTTING SOLVED FIELDS
-------------------------

Basic plot with WCS grid:
  python -m astroeasy.cli plot --image observation.fits --wcs solution.wcs

With Gaia catalog overlay:
  python -m astroeasy.cli plot \\
    --image observation.fits \\
    --wcs solution.wcs \\
    --gaia --gaia-limit 16

With matched stars from solve:
  python -m astroeasy.cli plot \\
    --image observation.fits \\
    --wcs image.wcs \\
    --matched image.rdls

Full example with all options:
  python -m astroeasy.cli plot \\
    --image observation.fits \\
    --wcs image.wcs \\
    --matched image.rdls \\
    --gaia --gaia-limit 18 --gaia-max 500 \\
    --title "NGC 1234 Field" \\
    --dpi 150 \\
    -o pretty_plot.png

Python API for plotting:
  from astroeasy import plot_solved_field, query_gaia_field, get_field_bounds_from_wcs
  from astropy.io import fits
  from astropy.wcs import WCS

  # Load WCS from solve result
  with fits.open("image.wcs") as hdul:
      wcs = WCS(hdul[0].header)

  # Query Gaia for catalog stars
  bounds = get_field_bounds_from_wcs(wcs, 4096, 4096)
  gaia_stars = query_gaia_field(*bounds, faint_limit=16.0)

  # Generate plot
  plot_solved_field(
      image_path="observation.fits",
      wcs=wcs,
      output_path="plot.png",
      catalog_stars=gaia_stars,
  )
"""
    print(examples_text)
    return 0


def _load_detections_from_file(path: str) -> list[Detection]:
    """Load detections from a CSV or tab-separated file.

    Supports:
    - CSV files with header row (columns: x, y, flux)
    - Tab-separated files with header row (columns: x, y, flux)
    - Tab-separated files without header (columns assumed: x, y, flux)

    The delimiter is auto-detected by examining the first line.
    Header presence is determined by checking if the first field is numeric.
    """
    detections = []
    with open(path) as f:
        first_line = f.readline()
        f.seek(0)  # Reset to beginning

        # Detect delimiter: count tabs vs commas in first line
        tab_count = first_line.count("\t")
        comma_count = first_line.count(",")
        delimiter = "\t" if tab_count > comma_count else ","

        # Check if first field is numeric (no header) or text (has header)
        first_field = first_line.split(delimiter)[0].strip()
        has_header = not _is_numeric(first_field)

        if has_header:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                x = float(row.get("x", row.get("X", 0)))
                y = float(row.get("y", row.get("Y", 0)))
                flux = None
                if "flux" in row:
                    flux = float(row["flux"])
                elif "FLUX" in row:
                    flux = float(row["FLUX"])
                elif "counts" in row:
                    flux = float(row["counts"])
                elif "COUNTS" in row:
                    flux = float(row["COUNTS"])
                detections.append(Detection(x=x, y=y, flux=flux))
        else:
            # No header: assume columns are x, y, flux
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                if len(row) < 2:
                    continue
                x = float(row[0])
                y = float(row[1])
                flux = float(row[2]) if len(row) > 2 else None
                detections.append(Detection(x=x, y=y, flux=flux))

    return detections


def _is_numeric(s: str) -> bool:
    """Check if a string represents a numeric value (including scientific notation)."""
    try:
        float(s)
        return True
    except ValueError:
        return False


def cmd_build_tetra3_db(args: argparse.Namespace) -> int:
    """Build a tetra3 pattern DB from the local Gaia mirror (cascade WS-I)."""
    from astroeasy.cascade.tetra3db import build_tetra3_db

    path = build_tetra3_db(
        args.mirror, args.out,
        max_fov_deg=args.fov, mag_limit=args.mag_limit, force=args.force,
    )
    print(f"tetra3 DB: {path}")
    return 0


def cmd_build_index(args: argparse.Namespace) -> int:
    """Build custom astrometry.net indices from the local Gaia mirror (cascade WS-B)."""
    from astroeasy.cascade.index_build import build_custom_index
    from astroeasy.indices import scales_for_fov

    scales = scales_for_fov(args.fov)
    print(f"building scales {scales} at G<={args.depth} -> {args.out}")
    paths = build_custom_index(
        args.mirror, args.out,
        scales=scales, depth_g=args.depth,
        docker_image=args.docker, force=args.force,
    )
    for p in paths:
        print(f"  {p}")
    print(f'use with: indices_path={args.out} indices_series="CUSTOM"')
    return 0


def cmd_characterize(args: argparse.Namespace) -> int:
    """Characterize a sensor from blind solves of sidereal frames (cascade WS-G)."""
    from astroeasy.cascade.characterize import characterize_sensor

    config = AstrometryConfig(
        indices_path=Path(args.indices_path),
        indices_series=args.indices_series,
        docker_image=args.docker,
        min_width_degrees=args.scale_low,
        max_width_degrees=args.scale_high,
        cpulimit_seconds=args.cpulimit,
    )
    result = characterize_sensor(
        args.fits, config,
        sensor_id=args.sensor_id, out_dir=args.out, mirror_dir=args.mirror,
        build_db=not args.no_db, db_mag=args.db_mag, build_index=args.build_index,
    )
    p = result.profile
    print(f"sensor {p.sensor_id}: {len(result.frames)}/{len(args.fits)} frames solved")
    print(f"  pixel scale : {p.pixel_scale_arcsec:.4f} arcsec/px")
    print(f"  fov         : {p.fov_degrees:.4f} deg  (bounds {p.scale_bounds_degrees})")
    print(f"  rotation    : {p.rotation_prior_deg if p.rotation_prior_deg is not None else 'per-frame (no prior)'}")
    print(f"  parity      : {p.parity:+d}")
    print(f"  depth       : {f'G{p.mag_depth_g:.1f}' if p.mag_depth_g else 'unmeasured'}")
    if result.tetra3_db_path:
        print(f"  tetra3 DB   : {result.tetra3_db_path}")
    if result.custom_index_dir:
        print(f"  custom index: {result.custom_index_dir}")
    print(f"  profile     : {Path(args.out) / (p.sensor_id + '.yaml')}")
    return 0


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="astroeasy",
        description="Astrometry.net made easy - plate solving and index management",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # indices subcommand
    indices_parser = subparsers.add_parser("indices", help="Manage astrometry.net index files")
    indices_subparsers = indices_parser.add_subparsers(dest="indices_command")

    # indices download
    download_parser = indices_subparsers.add_parser("download", help="Download index files")
    download_parser.add_argument(
        "--series",
        type=str,
        default="5200_LITE",
        choices=[s.value for s in AstrometryIndexSeries],
        help="Index series to download (default: 5200_LITE)",
    )
    download_parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output directory for indices",
    )
    download_parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Number of concurrent downloads (default: 5)",
    )
    download_parser.add_argument(
        "--fov",
        type=float,
        default=None,
        help="Field of view in degrees; only download index files for scales matching this FOV",
    )

    # indices examine
    examine_parser = indices_subparsers.add_parser("examine", help="Examine index files")
    examine_parser.add_argument(
        "--series",
        type=str,
        default="5200_LITE",
        choices=[s.value for s in AstrometryIndexSeries],
        help="Index series to validate (default: 5200_LITE)",
    )
    examine_parser.add_argument(
        "--path",
        "-p",
        type=str,
        required=True,
        help="Path to indices directory",
    )
    examine_parser.add_argument(
        "--fov",
        type=float,
        default=None,
        help="Field of view in degrees; only check index files for scales matching this FOV",
    )

    # indices recommend
    recommend_parser = indices_subparsers.add_parser(
        "recommend", help="Recommend index series and files for a given FOV"
    )
    recommend_parser.add_argument(
        "--fov",
        type=float,
        required=True,
        help="Field of view in degrees",
    )
    recommend_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Write a sample astroeasy config YAML to this path",
    )
    recommend_parser.add_argument(
        "--series",
        type=str,
        default="5200_LITE",
        choices=[s.value for s in AstrometryIndexSeries],
        help="Index series to use in generated config (default: 5200_LITE)",
    )
    recommend_parser.add_argument(
        "--indices-path",
        type=str,
        default=None,
        help="Indices directory path to use in generated config",
    )

    # solve subcommand
    solve_parser = subparsers.add_parser(
        "solve", help="Solve astrometry for an image or source list"
    )
    solve_parser.add_argument(
        "--config",
        "-c",
        type=str,
        help="Path to YAML configuration file",
    )
    solve_parser.add_argument(
        "--image",
        type=str,
        help="FITS image to solve",
    )
    solve_parser.add_argument(
        "--xylist",
        type=str,
        help="Source list file (CSV or tab-separated, with or without header). "
        "Expected columns: x, y, flux/counts",
    )
    solve_parser.add_argument("--width", type=int, help="Image width in pixels")
    solve_parser.add_argument("--height", type=int, help="Image height in pixels")
    solve_parser.add_argument("--ra", type=float, help="Approximate RA hint (degrees)")
    solve_parser.add_argument("--dec", type=float, help="Approximate Dec hint (degrees)")
    solve_parser.add_argument(
        "--indices-path",
        type=str,
        help="Path to astrometry.net index files",
    )
    solve_parser.add_argument(
        "--docker-image",
        type=str,
        help="Docker image for astrometry.net (omit for local)",
    )
    solve_parser.add_argument(
        "--scale-low",
        type=float,
        default=0.1,
        help="Minimum field width in degrees (default: 0.1)",
    )
    solve_parser.add_argument(
        "--scale-high",
        type=float,
        default=10.0,
        help="Maximum field width in degrees (default: 10.0)",
    )
    solve_parser.add_argument(
        "--cpulimit",
        type=int,
        default=30,
        help="CPU time limit in seconds (default: 30)",
    )
    solve_parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Try multiple max_sources values (25, 50, 100, 200, 500) until solve succeeds",
    )
    solve_parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        help="Directory to save astrometry.net output files (.wcs, .rdls, .match, etc.)",
    )

    # build-config subcommand
    build_config_parser = subparsers.add_parser(
        "build-config",
        help="Create a YAML configuration file interactively or from CLI args",
    )
    build_config_parser.add_argument(
        "--indices-path",
        type=str,
        help="Path to astrometry.net index files",
    )
    build_config_parser.add_argument(
        "--indices-series",
        type=str,
        choices=[s.value for s in AstrometryIndexSeries],
        help="Index series (auto-detected from files if not specified)",
    )
    build_config_parser.add_argument(
        "--docker-image",
        type=str,
        help="Docker image for astrometry.net (omit for local installation)",
    )
    build_config_parser.add_argument(
        "--scale-low",
        type=float,
        default=0.1,
        help="Minimum field width in degrees (default: 0.1)",
    )
    build_config_parser.add_argument(
        "--scale-high",
        type=float,
        default=10.0,
        help="Maximum field width in degrees (default: 10.0)",
    )
    build_config_parser.add_argument(
        "--cpulimit",
        type=int,
        default=30,
        help="CPU time limit in seconds (default: 30)",
    )
    build_config_parser.add_argument(
        "--max-sources",
        type=int,
        default=100,
        help="Maximum sources for solving (default: 100)",
    )
    build_config_parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output YAML file path (default: astroeasy.yaml)",
    )
    build_config_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Don't prompt for missing values (requires --indices-path)",
    )

    # examples subcommand
    subparsers.add_parser(
        "examples",
        help="Show usage examples for CLI and Python API",
    )

    # plot subcommand
    plot_parser = subparsers.add_parser(
        "plot",
        help="Generate a visualization of a solved field with WCS grid overlay",
    )
    plot_parser.add_argument(
        "--image",
        "-i",
        type=str,
        required=True,
        help="FITS image file to plot",
    )
    plot_parser.add_argument(
        "--wcs",
        "-w",
        type=str,
        help="WCS FITS file (if separate from image)",
    )
    plot_parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Output image file (default: <image>.png)",
    )
    plot_parser.add_argument(
        "--matched",
        "-m",
        type=str,
        help="RDLS file with matched stars to overlay",
    )
    plot_parser.add_argument(
        "--gaia",
        action="store_true",
        help="Query and overlay Gaia catalog stars (requires astroquery)",
    )
    plot_parser.add_argument(
        "--gaia-limit",
        type=float,
        default=16.0,
        help="Gaia magnitude limit (default: 16.0)",
    )
    plot_parser.add_argument(
        "--gaia-max",
        type=int,
        default=200,
        help="Maximum Gaia stars to show (default: 200)",
    )
    plot_parser.add_argument(
        "--no-grid",
        action="store_true",
        help="Don't show RA/Dec grid lines",
    )
    plot_parser.add_argument(
        "--dpi",
        type=int,
        help="Output DPI (default: auto based on image size)",
    )
    plot_parser.add_argument(
        "--title",
        type=str,
        help="Title to display on the plot",
    )

    # test-install subcommand
    test_parser = subparsers.add_parser("test-install", help="Test astrometry.net installation")
    test_parser.add_argument(
        "--docker",
        type=str,
        help="Docker image to test (omit to test local installation)",
    )
    test_parser.add_argument(
        "--local",
        action="store_true",
        help="Test local installation (default if --docker not specified)",
    )

    # build-tetra3-db subcommand (cascade / WS-I)
    t3db_parser = subparsers.add_parser(
        "build-tetra3-db",
        help="Build a tetra3 pattern DB from a local Gaia mirror (cascade T1)",
    )
    t3db_parser.add_argument("--mirror", required=True, help="Local Gaia mirror directory")
    t3db_parser.add_argument("--out", required=True, help="Output DB path (.npz)")
    t3db_parser.add_argument("--fov", type=float, required=True, help="Sensor field width (deg)")
    t3db_parser.add_argument(
        "--mag-limit", type=float, default=None,
        help="Catalog depth (default: FoV-driven heuristic)",
    )
    t3db_parser.add_argument("--force", action="store_true", help="Rebuild even if cached")

    # build-index subcommand (cascade / WS-B)
    bidx_parser = subparsers.add_parser(
        "build-index",
        help="Build custom scale-matched astrometry.net indices from a local Gaia mirror",
    )
    bidx_parser.add_argument("--mirror", required=True, help="Local Gaia mirror directory")
    bidx_parser.add_argument("--out", required=True, help="Output indices directory")
    bidx_parser.add_argument("--fov", type=float, required=True, help="Sensor field width (deg)")
    bidx_parser.add_argument("--depth", type=float, required=True, help="Star depth (G mag)")
    bidx_parser.add_argument("--docker", type=str, default=None, help="astrometry-cli image")
    bidx_parser.add_argument("--force", action="store_true", help="Rebuild even if cached")

    # characterize subcommand (cascade / WS-G)
    char_parser = subparsers.add_parser(
        "characterize",
        help="Blind-solve sidereal frames, measure the sensor, write a profile + artifacts",
    )
    char_parser.add_argument("fits", nargs="+", help="Sidereal FITS frames")
    char_parser.add_argument("--sensor-id", required=True, help="Profile name (e.g. dao01)")
    char_parser.add_argument("--out", required=True, help="Output dir (profile + artifacts)")
    char_parser.add_argument("--indices-path", required=True, help="STOCK indices for blind solving")
    char_parser.add_argument("--indices-series", default="4200", help="Stock series (default 4200)")
    char_parser.add_argument("--docker", type=str, default=None, help="astrometry-cli image")
    char_parser.add_argument("--mirror", default=None, help="Local Gaia mirror (depth + artifacts)")
    char_parser.add_argument("--scale-low", type=float, default=0.1, help="Blind scale low (deg)")
    char_parser.add_argument("--scale-high", type=float, default=30.0, help="Blind scale high (deg)")
    char_parser.add_argument("--cpulimit", type=int, default=300, help="Per-frame CPU limit (s)")
    char_parser.add_argument("--no-db", action="store_true", help="Skip tetra3 DB build")
    char_parser.add_argument("--db-mag", type=float, default=None, help="tetra3 DB depth override")
    char_parser.add_argument(
        "--build-index", action="store_true",
        help="Also build a custom astrometry.net index (slow; needs --mirror)",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "indices":
        if args.indices_command == "download":
            return cmd_indices_download(args)
        elif args.indices_command == "examine":
            return cmd_indices_examine(args)
        elif args.indices_command == "recommend":
            return cmd_indices_recommend(args)
        else:
            indices_parser.print_help()
            return 1
    elif args.command == "solve":
        return cmd_solve(args)
    elif args.command == "build-config":
        return cmd_build_config(args)
    elif args.command == "examples":
        return cmd_examples(args)
    elif args.command == "plot":
        return cmd_plot(args)
    elif args.command == "test-install":
        return cmd_test_install(args)
    elif args.command == "build-tetra3-db":
        return cmd_build_tetra3_db(args)
    elif args.command == "build-index":
        return cmd_build_index(args)
    elif args.command == "characterize":
        return cmd_characterize(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
