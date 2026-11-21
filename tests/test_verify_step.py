"""The verify/re-tweak step must see the indices the solve saw, and must not fail silently.

astroeasy runs solve-field twice: the solve, then ``solve-field --verify <wcs> ...`` to verify the
solution and re-tweak its SIP terms. Through 1.2.x the verify step was given no ``--config``, so it
read solve-field's DEFAULT config file. On a host whose default config happened to list the index
files it re-tweaked the WCS; on any other host it failed, and the first-pass WCS was kept without a
word. Identical inputs gave different solutions (up to a pixel at the image centre), decided by a
file outside the caller's control.
"""

import subprocess
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astroeasy import AstrometryConfig, Detection, ImageMetadata, solve_field, solve_field_image
from astroeasy.dotnet import docker as dotnet_docker
from astroeasy.dotnet import runner as dotnet_runner
from astroeasy.dotnet.local import run_against_local


@pytest.fixture
def recorded_commands(monkeypatch):
    """Replace both solve-field runners with recorders; they report "no solution"."""
    calls: list[dict] = []

    def record(solve_command, run_directory, verify_command):
        calls.append(
            {
                "solve": solve_command,
                "verify": verify_command,
                "cfg": (Path(run_directory) / "astrometry.cfg").read_text(),
            }
        )
        return False

    monkeypatch.setattr(
        dotnet_runner,
        "run_against_local",
        lambda solve, run_dir, verify=None: record(solve, run_dir, verify),
    )
    monkeypatch.setattr(
        dotnet_runner,
        "run_against_container",
        lambda solve, run_dir, indices, image, verify=None, output_dir=None: record(
            solve, run_dir, verify
        ),
    )
    # The solver-version pin is tested on its own; keep it out of the way here.
    monkeypatch.setattr(dotnet_runner, "ensure_pinned_solver", lambda docker_image: None, raising=False)
    return calls


@pytest.fixture
def detections() -> list[Detection]:
    rng = np.random.default_rng(0)
    return [
        Detection(x=float(x), y=float(y), flux=float(f))
        for x, y, f in zip(
            rng.uniform(0, 1024, 30), rng.uniform(0, 1024, 30), rng.uniform(1, 9, 30), strict=True
        )
    ]


@pytest.fixture
def metadata() -> ImageMetadata:
    return ImageMetadata(width=1024, height=1024)


@pytest.fixture
def image_file(tmp_path) -> Path:
    path = tmp_path / "frame.fits"
    fits.PrimaryHDU(np.zeros((64, 64), dtype=np.float32)).writeto(path)
    return path


def config_for(tmp_path, docker_image=None) -> AstrometryConfig:
    return AstrometryConfig(indices_path=tmp_path, docker_image=docker_image, cpulimit_seconds=45)


def tokens(command: str) -> list[str]:
    return command.split()


def has_option(command: str, option: str, value: str) -> bool:
    words = tokens(command)
    return any(a == option and b == value for a, b in zip(words, words[1:], strict=False))


class TestVerifyGetsTheGeneratedConfig:
    """Both steps must read the cfg astroeasy writes, never solve-field's default one."""

    def test_star_list_solve(self, recorded_commands, detections, metadata, tmp_path):
        solve_field(detections, metadata, config_for(tmp_path))

        (call,) = recorded_commands
        assert has_option(call["solve"], "--config", "astrometry.cfg")
        assert has_option(call["verify"], "--config", "astrometry.cfg")

    def test_image_solve(self, recorded_commands, image_file, tmp_path):
        solve_field_image(image_file, config_for(tmp_path))

        (call,) = recorded_commands
        assert has_option(call["solve"], "--config", "astrometry.cfg")
        assert has_option(call["verify"], "--config", "astrometry.cfg")

    def test_docker_solve(self, recorded_commands, detections, metadata, tmp_path):
        solve_field(detections, metadata, config_for(tmp_path, docker_image="astrometry-cli"))

        (call,) = recorded_commands
        assert has_option(call["verify"], "--config", "astrometry.cfg")

    def test_the_cfg_lists_the_configured_indices(self, recorded_commands, detections, metadata, tmp_path):
        solve_field(detections, metadata, config_for(tmp_path))

        (call,) = recorded_commands
        assert f"add_path {tmp_path}" in call["cfg"].splitlines()


class TestVerifyIsBounded:
    """The verify step gets the same CPU limit as the solve.

    It used to inherit whatever limit the default config set (or none).
    """

    def test_star_list_solve(self, recorded_commands, detections, metadata, tmp_path):
        solve_field(detections, metadata, config_for(tmp_path))

        (call,) = recorded_commands
        assert has_option(call["verify"], "--cpulimit", "45")

    def test_image_solve(self, recorded_commands, image_file, tmp_path):
        solve_field_image(image_file, config_for(tmp_path))

        (call,) = recorded_commands
        assert has_option(call["verify"], "--cpulimit", "45")


class TestLocalVerifyFailureIsNotSilent:
    """run_against_local: a solve only counts if its verify step also succeeded.

    Plain shell commands stand in for solve-field: the "solve" leaves a .match file, as a solved
    field does, and the "verify" succeeds or fails.
    """

    def test_failed_verify_fails_the_solve(self, tmp_path):
        assert run_against_local("touch sources.match", tmp_path, "false") is False

    def test_successful_verify_keeps_the_solve(self, tmp_path):
        assert run_against_local("touch sources.match", tmp_path, "true") is True

    def test_no_verify_keeps_the_solve(self, tmp_path):
        assert run_against_local("touch sources.match", tmp_path, None) is True

    def test_verify_does_not_run_when_the_solve_failed(self, tmp_path):
        assert run_against_local("true", tmp_path, "touch verified") is False
        assert not (tmp_path / "verified").exists()


class TestContainerVerifyFailureIsNotSilent:
    """run_against_container: same rule, with docker replaced by a fake subprocess.run."""

    @pytest.fixture
    def fake_docker(self, monkeypatch, tmp_path):
        def make(verify_returncode: int):
            def fake_run(cmd, **kwargs):
                if "--verify" not in cmd:  # the solve: leave a .match, as a solved field does
                    (tmp_path / "sources.match").touch()
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, verify_returncode, "", "engine failed")

            monkeypatch.setattr(dotnet_docker.subprocess, "run", fake_run)

        return make

    def test_failed_verify_fails_the_solve(self, fake_docker, tmp_path):
        fake_docker(verify_returncode=255)
        ok = dotnet_docker.run_against_container(
            "solve-field sources.fits", tmp_path, tmp_path, "img", "solve-field --verify sources.wcs"
        )
        assert ok is False

    def test_successful_verify_keeps_the_solve(self, fake_docker, tmp_path):
        fake_docker(verify_returncode=0)
        ok = dotnet_docker.run_against_container(
            "solve-field sources.fits", tmp_path, tmp_path, "img", "solve-field --verify sources.wcs"
        )
        assert ok is True


class TestVerifyCanBeTurnedOff:
    """`verify_solution=False` skips the verify/re-tweak step, so a caller can choose either behaviour
    from configuration alone, the same on every host. The default runs it, as astroeasy always meant to."""

    def test_default_runs_the_verify_step(self, recorded_commands, detections, metadata, tmp_path):
        solve_field(detections, metadata, config_for(tmp_path))

        (call,) = recorded_commands
        assert call["verify"] is not None

    def test_star_list_solve_without_verify(self, recorded_commands, detections, metadata, tmp_path):
        config = AstrometryConfig(indices_path=tmp_path, docker_image=None, verify_solution=False)
        solve_field(detections, metadata, config)

        (call,) = recorded_commands
        assert call["solve"] is not None
        assert call["verify"] is None

    def test_image_solve_without_verify(self, recorded_commands, image_file, tmp_path):
        config = AstrometryConfig(indices_path=tmp_path, docker_image=None, verify_solution=False)
        solve_field_image(image_file, config)

        (call,) = recorded_commands
        assert call["verify"] is None

    def test_docker_solve_without_verify(self, recorded_commands, detections, metadata, tmp_path):
        config = AstrometryConfig(indices_path=tmp_path, docker_image="astrometry-cli", verify_solution=False)
        solve_field(detections, metadata, config)

        (call,) = recorded_commands
        assert call["verify"] is None
