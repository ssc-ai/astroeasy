"""Docker-based execution of astrometry.net solve-field."""

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def run_against_container(
    solve_field_command: str | None,
    run_directory: Path,
    indices_path: Path,
    docker_image: str,
    verify_command: str | None = None,
    output_dir: Path | None = None,
) -> bool:
    """Run solve-field inside a Docker container.

    Args:
        solve_field_command: The solve-field command to execute. If None, assumes
            sources.wcs already exists and only runs verification.
        run_directory: Working directory containing sources.fits and config files.
        indices_path: Host path to astrometry.net index files.
        docker_image: Name of the Docker image to use.
        verify_command: Optional verification command to run after successful solve.
        output_dir: Optional directory for logging output.

    Returns:
        True if solve was successful (sources.match file exists), False otherwise.
    """
    base_docker_command = [
        "docker",
        "run",
        "--rm",
        "--workdir=/home/starman",
        f"--mount=type=bind,source={indices_path},target=/usr/local/astrometry/data",
        f"--mount=type=bind,source={run_directory},target=/home/starman",
        docker_image,
    ]

    r1 = None
    if solve_field_command:
        logger.debug(f"[run_against_container] command: {solve_field_command}")

        # Run the solve-field command
        r1 = subprocess.run(
            base_docker_command + solve_field_command.split(),
            capture_output=True,
            text=True,
        )

        # Log stdout/stderr for debugging
        if r1.stdout:
            for line in r1.stdout.strip().split("\n")[-10:]:  # Last 10 lines
                logger.debug(f"[solve-field stdout] {line}")
        if r1.stderr:
            for line in r1.stderr.strip().split("\n")[-10:]:  # Last 10 lines
                logger.debug(f"[solve-field stderr] {line}")

        # Check for .match file (sources.match or image.match)
        match_files = list(run_directory.glob("*.match"))
        successful_fit = len(match_files) > 0
        logger.info(
            f"{'✅' if successful_fit else '❌'} [run_against_container] successful_fit: {successful_fit}"
        )

        if not successful_fit:
            # Log what files were created for debugging
            files = list(run_directory.iterdir())
            logger.debug(f"[run_against_container] files: {[f.name for f in files]}")
    else:
        logger.info("No solve-field command provided, checking for existing WCS")
        successful_fit = len(list(run_directory.glob("*.wcs"))) > 0

    # Store astrometry logs if output_dir is provided
    if output_dir and r1 is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        log_file = output_dir / "astrometry.txt"
        with open(log_file, "a") as f:
            f.write(f"astrometry run in directory: {run_directory}\n")
            f.write(f"Command: {solve_field_command}\n")
            f.write(f"Return code: {r1.returncode}\n")
            f.write(f"STDOUT:\n{r1.stdout}\n")
            f.write(f"STDERR:\n{r1.stderr}\n")
            f.write("-" * 80 + "\n")
        logger.info(f"Astrometry run logged to {log_file}")

    if verify_command and successful_fit:
        # Run the verification command
        r2 = subprocess.run(
            base_docker_command + verify_command.split(),
            capture_output=True,
            text=True,
        )
        logger.info("[run_against_container] verification step complete")

        # Log the verification output
        if output_dir:
            log_file = output_dir / "astrometry.txt"
            with open(log_file, "a") as f:
                f.write(f"Verification command: {verify_command}\n")
                f.write(f"Verification return code: {r2.returncode}\n")
                f.write(f"Verification STDOUT:\n{r2.stdout}\n")
                f.write(f"Verification STDERR:\n{r2.stderr}\n")
                f.write("-" * 80 + "\n")

        if r2.returncode != 0:
            # Same rule as run_against_local: a solve whose verify step failed is not a solve.
            logger.error(
                f"[run_against_container] verification step failed (solve-field returned "
                f"{r2.returncode}); discarding this solution"
            )
            for line in f"{r2.stdout}\n{r2.stderr}".strip().split("\n")[-10:]:
                logger.error(f"[solve-field verify] {line}")
            return False

    return successful_fit


def test_dotnet_install(image: str = "astrometry-cli") -> bool:
    """Test if solve-field works inside a Docker container.

    Args:
        image: Docker image name to test.

    Returns:
        True if solve-field is available and working, False otherwise.
    """
    try:
        # Run solve-field --help in a new container
        result = subprocess.run(
            ["docker", "run", "--rm", image, "solve-field", "--help"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error("Failed to run solve-field in container")
            return False

        # Get version information from the help output
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

    except subprocess.SubprocessError as e:
        logger.error(f"Failed to run docker container: {e}")
        return False


if __name__ == "__main__":
    test_dotnet_install()
