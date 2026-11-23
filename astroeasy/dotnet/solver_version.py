"""The astrometry.net release astroeasy is pinned to, and the check that holds every solve to it.

Different solve-field builds can return different solutions for identical input. A result is therefore
only reproducible if the solver is part of what an astroeasy version pins. Python dependency pins cannot
express a C program on the PATH, so astroeasy enforces it at run time instead:

- ``test_install()`` (and ``astroeasy test-install``) reports a solver of another revision as not
  correctly installed.
- Every solve refuses to run on one, raising :class:`SolverVersionError`, so the pin holds even for
  callers who never run the install check. The solver is asked once per process.

Set ``ASTROEASY_ALLOW_UNPINNED_SOLVER=1`` to run against another release anyway, e.g. to evaluate a
candidate for the next pin. Every such process logs a warning, because its results depend on the
solver installed on that host.

A MISSING solver is not this module's business: every solve already fails without one, and says so.
"""

import logging
import os
import re
import subprocess

from astroeasy.constants import ASTROMETRY_NET_VERSION

logger = logging.getLogger(__name__)

ALLOW_UNPINNED_ENV = "ASTROEASY_ALLOW_UNPINNED_SOLVER"

# solve-field prints "Revision 0.99, date Sun_Oct_11_17:24:20_2026_-0400." A build from an untagged
# commit prints the git-describe string ("Revision 0.99-3-gaea5f36, ..."), which is not the release.
_REVISION = re.compile(r"Revision\s+([^,\s]+)")

_HOW_TO_FIX = (
    f"Install astrometry.net {ASTROMETRY_NET_VERSION} (build the {ASTROMETRY_NET_VERSION} tag of "
    "https://github.com/dstndstn/astrometry.net, or use the astroeasy Docker image), or set "
    f"{ALLOW_UNPINNED_ENV}=1 to run anyway"
)

# Solvers (None = local, else the Docker image) already found acceptable in this process.
_verified: set[str | None] = set()


class SolverVersionError(RuntimeError):
    """solve-field reports a different astrometry.net release than astroeasy is pinned to."""


def parse_solver_revision(output: str) -> str | None:
    """Extract the revision from solve-field's help text.

    Args:
        output: solve-field's stdout/stderr.

    Returns:
        The revision string (e.g. ``"0.99"``), or None when the text has no revision line.
    """
    match = _REVISION.search(output)
    return match.group(1) if match else None


def read_solver_revision(docker_image: str | None = None) -> str | None:
    """Ask solve-field which astrometry.net revision it is.

    Args:
        docker_image: Image to ask, or None for the solve-field on this host's PATH.

    Returns:
        The revision, or None when solve-field cannot be run or prints no revision.
    """
    command = ["solve-field", "--help"]
    if docker_image is not None:
        command = ["docker", "run", "--rm", docker_image, *command]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_solver_revision(f"{result.stdout}\n{result.stderr}")


def solver_version_problem(revision: str | None, pinned: str = ASTROMETRY_NET_VERSION) -> str | None:
    """Say what is wrong with a solver revision, if anything.

    Args:
        revision: What solve-field reports, or None if it reported nothing readable.
        pinned: The release astroeasy is pinned to.

    Returns:
        None for the pinned release, otherwise a one-line description of the mismatch.
    """
    if revision is None:
        return f"could not read solve-field's revision (astroeasy requires astrometry.net {pinned})"
    if revision != pinned:
        return f"solve-field is astrometry.net {revision}, but astroeasy requires {pinned}"
    return None


def unpinned_allowed() -> bool:
    """Whether the caller has opted out of the solver pin for this process."""
    return os.environ.get(ALLOW_UNPINNED_ENV, "").strip().lower() in {"1", "true", "yes"}


def solver_is_acceptable(revision: str | None) -> bool:
    """Report on a solver revision for an install check, and say whether to accept it.

    Args:
        revision: What solve-field reports.

    Returns:
        True for the pinned release, or for any release when the opt-out is set.
    """
    problem = solver_version_problem(revision)
    if problem is None:
        logger.info(f"solve-field (Astrometry.net) version: {revision} (the pinned release)")
        return True
    if unpinned_allowed():
        logger.warning(f"{problem}; accepting it because {ALLOW_UNPINNED_ENV} is set")
        return True
    logger.error(f"{problem}. {_HOW_TO_FIX}.")
    return False


def ensure_pinned_solver(docker_image: str | None) -> None:
    """Refuse to solve on any astrometry.net release but the pinned one.

    Args:
        docker_image: The solver that will run: None for the local solve-field, else the image.

    Raises:
        SolverVersionError: solve-field reports another release and the opt-out is not set.
    """
    if docker_image in _verified:
        return
    revision = read_solver_revision(docker_image)
    if revision is None:
        return  # missing or unreadable: the solve itself fails and says why
    problem = solver_version_problem(revision)
    if problem is not None:
        if not unpinned_allowed():
            raise SolverVersionError(f"{problem}. {_HOW_TO_FIX}.")
        logger.warning(
            f"{problem}; solving anyway because {ALLOW_UNPINNED_ENV} is set. Results from this "
            "process depend on the solver installed on this host."
        )
    _verified.add(docker_image)


def reset_verified_solvers() -> None:
    """Forget which solvers were checked (for tests, or after swapping the solver on PATH)."""
    _verified.clear()
