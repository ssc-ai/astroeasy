import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# astrometry.net's solve-field shells out to the system python for helper
# scripts (e.g. astrometry.util.removelines). Disable user site-packages so
# those scripts use the system packages they were built against, rather than
# picking up incompatible versions from ~/.local (e.g. NumPy 2.x).
_SOLVE_ENV = {**os.environ, "PYTHONNOUSERSITE": "1"}


def run_against_local(
    solve_field_command: str | None,
    run_directory: Path,
    verify_command: str | None = None,
):
    """Run solve-field locally.

    Args:
        solve_field_command: The solve-field command to run, or None to skip.
        run_directory: Directory containing input files and for output.
        verify_command: Optional verification command to run after solving.

    Returns:
        True if solve was successful, False otherwise.
    """
    logger.debug(f"[run_against_local] run_directory: {run_directory}")

    # Run the first solve-field command
    if solve_field_command:
        logger.debug(f"[run_against_local] command: {solve_field_command}")
        result = subprocess.run(
            solve_field_command.split(),
            capture_output=True,
            text=True,
            cwd=run_directory,
            env=_SOLVE_ENV,
        )

        # Log stdout/stderr for debugging
        if result.stdout:
            for line in result.stdout.strip().split("\n")[-10:]:  # Last 10 lines
                logger.debug(f"[solve-field stdout] {line}")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-10:]:  # Last 10 lines
                logger.debug(f"[solve-field stderr] {line}")

        if result.returncode != 0:
            logger.warning(f"[run_against_local] solve-field returned {result.returncode}")

    # Check for .match file (sources.match or image.match)
    match_files = list(run_directory.glob("*.match"))
    successful_fit = len(match_files) > 0

    logger.info(f"[run_against_local] successful_fit: {successful_fit}")

    if not successful_fit:
        # Log what files were created for debugging
        files = list(run_directory.iterdir())
        logger.debug(f"[run_against_local] files in run_directory: {[f.name for f in files]}")

    if verify_command and successful_fit:
        # Run the verification command
        result = subprocess.run(
            verify_command.split(),
            capture_output=True,
            text=True,
            cwd=run_directory,
            env=_SOLVE_ENV,
        )
        logger.info("[run_against_local] verification step complete")

    return successful_fit


def test_dotnet_install():
    try:
        # Check if solve-field exists in PATH using which/where command
        which_cmd = "where" if subprocess.os.name == "nt" else "which"
        location_result = subprocess.run([which_cmd, "solve-field"], capture_output=True, text=True)

        if location_result.returncode != 0:
            logger.error("solve-field (Astrometry.net) is not installed or not in PATH")
            return False

        solve_field_path = location_result.stdout.strip()
        logger.info(f"solve-field found at: {solve_field_path}")

        # Get version information
        result = subprocess.run(["solve-field"], capture_output=True, text=True)
        revision_match = re.search(r"Revision\s+([\d.]+)", result.stdout)

        if revision_match:
            version = revision_match.group(1)
            logger.info(f"solve-field (Astrometry.net) version: {version}")
            return True
        else:
            logger.warning(
                "solve-field (Astrometry.net) is installed but version could not be determined"
            )
            return True

    except FileNotFoundError:
        logger.error("solve-field (Astrometry.net) is not installed or not in PATH")
        return False


if __name__ == "__main__":
    test_dotnet_install()
