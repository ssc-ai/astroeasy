"""Pytest configuration and fixtures for astroeasy tests."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from astroeasy import AstrometryConfig, Detection, ImageMetadata
from astroeasy.constants import AstrometryIndexSeries
from astroeasy.runner import test_install

# Test data directory
TEST_DATA_DIR = Path(__file__).parent / "data"

# Astrometry.net index files (5200-LITE series). Override with
# ASTROEASY_TEST_INDICES if yours live elsewhere; tests needing indices skip
# themselves when the path holds no *.fits.
DEFAULT_INDICES_PATH = Path(
    os.environ.get("ASTROEASY_TEST_INDICES", "/stars/data/share/5000/5200-LITE")
)


def _check_local_install() -> bool:
    """Check if local astrometry.net is available."""
    try:
        return test_install(docker_image=None)
    except Exception:
        return False


def _check_docker_install(image: str = "astrometry-cli") -> bool:
    """Check if Docker astrometry.net is available."""
    try:
        return test_install(docker_image=image)
    except Exception:
        return False


def _check_indices_available(path: Path) -> bool:
    """Check if index files are available at the given path."""
    return path.exists() and any(path.glob("*.fits"))


# Pytest markers for conditional test skipping
requires_local_install = pytest.mark.skipif(
    not _check_local_install(),
    reason="Local astrometry.net installation not available",
)

requires_docker_install = pytest.mark.skipif(
    not _check_docker_install(),
    reason="Docker astrometry-cli image not available",
)

requires_indices = pytest.mark.skipif(
    not _check_indices_available(DEFAULT_INDICES_PATH),
    reason=f"Index files not available at {DEFAULT_INDICES_PATH}",
)


@pytest.fixture
def test_data_dir() -> Path:
    """Return the test data directory."""
    return TEST_DATA_DIR


@pytest.fixture
def sample_detections() -> list[Detection]:
    """Load sample detections from test data file."""
    data_file = TEST_DATA_DIR / "x_y_counts_1024_1024.txt"
    data = np.loadtxt(data_file, delimiter="\t", dtype=float)

    return [Detection(x=row[0], y=row[1], flux=row[2]) for row in data]


@pytest.fixture
def sample_metadata() -> ImageMetadata:
    """Return sample image metadata matching the test detections."""
    return ImageMetadata(width=1024, height=1024)


@pytest.fixture
def indices_path() -> Path:
    """Return the path to index files."""
    return DEFAULT_INDICES_PATH


@pytest.fixture
def local_config(indices_path: Path) -> AstrometryConfig:
    """Return a config for local astrometry.net execution."""
    return AstrometryConfig(
        indices_path=indices_path,
        indices_series=AstrometryIndexSeries.SERIES_5200_LITE,
        docker_image=None,
        cpulimit_seconds=60,
    )


@pytest.fixture
def docker_config(indices_path: Path) -> AstrometryConfig:
    """Return a config for Docker astrometry.net execution."""
    return AstrometryConfig(
        indices_path=indices_path,
        indices_series=AstrometryIndexSeries.SERIES_5200_LITE,
        docker_image="astrometry-cli",
        cpulimit_seconds=60,
    )


@pytest.fixture
def minimal_detections() -> list[Detection]:
    """Return a minimal set of detections (too few to solve)."""
    return [
        Detection(x=100.0, y=200.0, flux=1000.0),
        Detection(x=500.0, y=300.0, flux=800.0),
    ]
