"""Tests for astroeasy configuration."""

import tempfile
from pathlib import Path

import pytest

from astroeasy import AstrometryConfig
from astroeasy.constants import AstrometryIndexSeries


class TestAstrometryConfig:
    """Tests for the AstrometryConfig dataclass."""

    def test_create_minimal(self):
        """Test creating config with just required fields."""
        config = AstrometryConfig(indices_path=Path("/data/indices"))
        assert config.indices_path == Path("/data/indices")
        assert config.indices_series == AstrometryIndexSeries.SERIES_5200_LITE
        assert config.cpulimit_seconds == 30
        assert config.docker_image is None

    def test_create_full(self):
        """Test creating config with all fields."""
        config = AstrometryConfig(
            indices_path=Path("/data/indices"),
            indices_series=AstrometryIndexSeries.SERIES_5200,
            cpulimit_seconds=60,
            min_width_degrees=0.5,
            max_width_degrees=5.0,
            tweak_order=3,
            max_sources=200,
            min_sources_for_attempt=6,
            docker_image="my-astrometry:latest",
            output_dir=Path("/tmp/output"),
        )
        assert config.indices_series == AstrometryIndexSeries.SERIES_5200
        assert config.cpulimit_seconds == 60
        assert config.min_width_degrees == 0.5
        assert config.max_width_degrees == 5.0
        assert config.tweak_order == 3
        assert config.max_sources == 200
        assert config.min_sources_for_attempt == 6
        assert config.docker_image == "my-astrometry:latest"
        assert config.output_dir == Path("/tmp/output")

    def test_string_path_conversion(self):
        """Test that string paths are converted to Path objects."""
        config = AstrometryConfig(
            indices_path="/data/indices",
            output_dir="/tmp/output",
        )
        assert isinstance(config.indices_path, Path)
        assert isinstance(config.output_dir, Path)

    def test_string_series_conversion(self):
        """Test that string series is converted to enum."""
        config = AstrometryConfig(
            indices_path="/data/indices",
            indices_series="5200_LITE",
        )
        assert config.indices_series == AstrometryIndexSeries.SERIES_5200_LITE

    def test_to_dict(self):
        """Test converting config to dictionary."""
        config = AstrometryConfig(
            indices_path=Path("/data/indices"),
            docker_image="astrometry-cli",
        )
        d = config.to_dict()
        assert d["indices_path"] == "/data/indices"
        assert d["docker_image"] == "astrometry-cli"
        assert d["cpulimit_seconds"] == 30

    def test_from_dict(self):
        """Test creating config from dictionary."""
        d = {
            "indices_path": "/data/indices",
            "docker_image": "astrometry-cli",
            "cpulimit_seconds": 45,
        }
        config = AstrometryConfig.from_dict(d)
        assert config.indices_path == Path("/data/indices")
        assert config.docker_image == "astrometry-cli"
        assert config.cpulimit_seconds == 45

    def test_from_dict_missing_indices_path(self):
        """Test that from_dict raises error without indices_path."""
        with pytest.raises(ValueError, match="indices_path is required"):
            AstrometryConfig.from_dict({})

    def test_yaml_roundtrip(self):
        """Test saving and loading YAML config."""
        config = AstrometryConfig(
            indices_path=Path("/data/indices"),
            docker_image="astrometry-cli",
            cpulimit_seconds=45,
            min_width_degrees=0.2,
        )

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            yaml_path = Path(f.name)

        try:
            config.to_yaml(yaml_path)
            loaded = AstrometryConfig.from_yaml(yaml_path)

            assert loaded.indices_path == config.indices_path
            assert loaded.docker_image == config.docker_image
            assert loaded.cpulimit_seconds == config.cpulimit_seconds
            assert loaded.min_width_degrees == config.min_width_degrees
        finally:
            yaml_path.unlink()

    def test_from_yaml_file_content(self):
        """Test loading config from YAML with specific content."""
        yaml_content = """
indices_path: /custom/path
indices_series: "5200"
docker_image: custom-image
cpulimit_seconds: 120
min_width_degrees: 1.0
max_width_degrees: 20.0
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            config = AstrometryConfig.from_yaml(yaml_path)
            assert config.indices_path == Path("/custom/path")
            assert config.docker_image == "custom-image"
            assert config.cpulimit_seconds == 120
            assert config.min_width_degrees == 1.0
            assert config.max_width_degrees == 20.0
        finally:
            yaml_path.unlink()


class TestAstrometryIndexSeries:
    """Tests for the AstrometryIndexSeries enum."""

    def test_all_series_exist(self):
        """Test that all expected series exist."""
        assert AstrometryIndexSeries.SERIES_5200
        assert AstrometryIndexSeries.SERIES_5200_LITE
        assert AstrometryIndexSeries.SERIES_4100
        assert AstrometryIndexSeries.SERIES_4200
        assert AstrometryIndexSeries.SERIES_5200_LITE_4100
        assert AstrometryIndexSeries.SERIES_CUSTOM

    def test_series_string_value(self):
        """Test that series can be converted to string."""
        assert str(AstrometryIndexSeries.SERIES_5200_LITE) == "5200_LITE"
        assert str(AstrometryIndexSeries.SERIES_5200) == "5200"

    def test_series_from_string(self):
        """Test creating series from string value."""
        series = AstrometryIndexSeries("5200_LITE")
        assert series == AstrometryIndexSeries.SERIES_5200_LITE
