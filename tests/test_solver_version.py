"""astroeasy is pinned to one astrometry.net release, so a given astroeasy version solves the same way
on every host.

Different solve-field builds can return different solutions for identical input, so a result is only
reproducible if the solver is part of the pin. The pin is ``ASTROMETRY_NET_VERSION``. A solver that
is present but reports another revision is refused, unless ``ASTROEASY_ALLOW_UNPINNED_SOLVER`` says
otherwise. A missing solver is not this check's business: every solve already fails without one.
"""

import logging

import pytest

from astroeasy import ASTROMETRY_NET_VERSION, SolverVersionError
from astroeasy.dotnet import local as dotnet_local
from astroeasy.dotnet import solver_version
from astroeasy.dotnet.solver_version import (
    ALLOW_UNPINNED_ENV,
    ensure_pinned_solver,
    parse_solver_revision,
    solver_version_problem,
)

OTHER = "0.93"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv(ALLOW_UNPINNED_ENV, raising=False)
    solver_version.reset_verified_solvers()
    yield
    solver_version.reset_verified_solvers()


@pytest.fixture
def installed(monkeypatch):
    """Pretend solve-field reports the given revision (None: not installed / unreadable)."""

    def install(revision):
        monkeypatch.setattr(solver_version, "read_solver_revision", lambda docker_image=None: revision)

    return install


class TestParseSolverRevision:
    def test_release_build(self):
        out = "Revision 0.93, date Mon_Dec_19_16:41:15_2022_-0500.\nUsage: solve-field ..."
        assert parse_solver_revision(out) == "0.93"

    def test_build_from_an_untagged_commit_is_not_mistaken_for_the_release(self):
        out = "Revision 0.99-3-gaea5f36, date Thu_Nov_19_2026.\n"
        assert parse_solver_revision(out) == "0.99-3-gaea5f36"

    def test_no_revision_line(self):
        assert parse_solver_revision("solve-field: command not found") is None


class TestSolverVersionProblem:
    def test_the_pinned_release_is_fine(self):
        assert solver_version_problem(ASTROMETRY_NET_VERSION) is None

    def test_another_release_names_both_versions(self):
        problem = solver_version_problem(OTHER)
        assert OTHER in problem and ASTROMETRY_NET_VERSION in problem

    def test_an_untagged_build_of_the_pinned_release_is_not_the_release(self):
        assert solver_version_problem(f"{ASTROMETRY_NET_VERSION}-3-gaea5f36") is not None

    def test_an_unreadable_revision_is_a_problem(self):
        assert solver_version_problem(None) is not None


class TestEnsurePinnedSolver:
    def test_pinned_release_passes(self, installed):
        installed(ASTROMETRY_NET_VERSION)
        ensure_pinned_solver(None)

    def test_other_release_is_refused(self, installed):
        installed(OTHER)
        with pytest.raises(SolverVersionError, match=OTHER):
            ensure_pinned_solver(None)

    def test_other_release_in_docker_is_refused(self, installed):
        installed(OTHER)
        with pytest.raises(SolverVersionError):
            ensure_pinned_solver("astrometry-cli")

    def test_override_runs_anyway_but_says_so(self, installed, monkeypatch, caplog):
        installed(OTHER)
        monkeypatch.setenv(ALLOW_UNPINNED_ENV, "1")
        with caplog.at_level(logging.WARNING):
            ensure_pinned_solver(None)
        assert OTHER in caplog.text

    def test_missing_solver_is_left_to_the_solve(self, installed):
        installed(None)
        ensure_pinned_solver(None)

    def test_the_solver_is_asked_once_per_process(self, monkeypatch):
        asked = []
        monkeypatch.setattr(
            solver_version,
            "read_solver_revision",
            lambda docker_image=None: asked.append(docker_image) or ASTROMETRY_NET_VERSION,
        )
        for _ in range(3):
            ensure_pinned_solver(None)
        assert asked == [None]


class TestInstallCheck:
    """`astroeasy test-install` / test_install() report a wrong solver as not installed correctly."""

    @pytest.fixture(autouse=True)
    def on_path(self, monkeypatch):
        monkeypatch.setattr(dotnet_local.shutil, "which", lambda name: f"/usr/bin/{name}")

    def test_pinned_release(self, installed):
        installed(ASTROMETRY_NET_VERSION)
        assert dotnet_local.test_dotnet_install() is True

    def test_other_release(self, installed):
        installed(OTHER)
        assert dotnet_local.test_dotnet_install() is False

    def test_other_release_with_override(self, installed, monkeypatch):
        installed(OTHER)
        monkeypatch.setenv(ALLOW_UNPINNED_ENV, "1")
        assert dotnet_local.test_dotnet_install() is True

    def test_not_on_path(self, installed, monkeypatch):
        installed(None)
        monkeypatch.setattr(dotnet_local.shutil, "which", lambda name: None)
        assert dotnet_local.test_dotnet_install() is False


class TestSolveRefusesTheWrongSolver:
    """The pin holds at solve time too, not only for callers who run the install check."""

    def test_solve_field(self, installed, tmp_path):
        from astroeasy import AstrometryConfig, Detection, ImageMetadata, solve_field

        installed(OTHER)
        detections = [Detection(x=float(i), y=float(2 * i), flux=1.0) for i in range(30)]
        with pytest.raises(SolverVersionError):
            solve_field(detections, ImageMetadata(width=64, height=64), AstrometryConfig(indices_path=tmp_path))


def test_the_docker_image_builds_the_pinned_release():
    """The Docker image and the local check must agree on the solver, or the image fails its own check."""
    import re
    from pathlib import Path

    import astroeasy.dotnet

    dockerfile = (Path(astroeasy.dotnet.__file__).parent / "Dockerfile").read_text()
    (ref,) = re.findall(r"^ARG ASTROMETRY_NET_REF=(\S+)$", dockerfile, flags=re.MULTILINE)
    assert ref == ASTROMETRY_NET_VERSION
