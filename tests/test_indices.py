"""Tests for astroeasy index management."""

import tempfile
from pathlib import Path

import pytest

from astroeasy.constants import (
    ASTROMETRY_4100_EXPECTED_STRUCTURE,
    ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE,
    AstrometryIndexSeries,
)
from astroeasy.indices import (
    examine_indices,
    filter_structure_by_scales,
    get_expected_structure,
    human_readable_size,
    scales_for_fov,
)


class TestHumanReadableSize:
    """Tests for the human_readable_size function."""

    def test_bytes(self):
        """Test formatting bytes."""
        assert human_readable_size(500) == "500.00 B"

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert human_readable_size(1024) == "1.00 KB"
        assert human_readable_size(1536) == "1.50 KB"

    def test_megabytes(self):
        """Test formatting megabytes."""
        assert human_readable_size(1024 * 1024) == "1.00 MB"

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert human_readable_size(1024 * 1024 * 1024) == "1.00 GB"

    def test_large_size(self):
        """Test formatting large sizes."""
        # 15 GB
        size = 15 * 1024 * 1024 * 1024
        result = human_readable_size(size)
        assert "GB" in result
        assert "15" in result


class TestGetExpectedStructure:
    """Tests for the get_expected_structure function."""

    def test_5200_lite(self):
        """Test getting structure for 5200_LITE series."""
        urls, structure = get_expected_structure(AstrometryIndexSeries.SERIES_5200_LITE)
        assert len(urls) == 1
        assert "5200" in urls[0]
        assert "LITE" in urls[0]
        assert structure == ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE

    def test_5200(self):
        """Test getting structure for 5200 series."""
        urls, structure = get_expected_structure(AstrometryIndexSeries.SERIES_5200)
        assert len(urls) == 1
        assert len(structure) > 0

    def test_4100(self):
        """Test getting structure for 4100 series."""
        urls, structure = get_expected_structure(AstrometryIndexSeries.SERIES_4100)
        assert len(urls) == 1
        assert len(structure) > 0
        # 4100 series files start with index-41
        assert any("4107" in f or "4108" in f for f in structure.keys())

    def test_combined_series(self):
        """Test getting structure for combined series."""
        urls, structure = get_expected_structure(AstrometryIndexSeries.SERIES_5200_LITE_4100)
        assert len(urls) == 2
        # Should have files from both series
        has_5200 = any("5200" in f for f in structure.keys())
        has_4100 = any("4107" in f or "4108" in f for f in structure.keys())
        assert has_5200 and has_4100

    def test_custom_series(self):
        """Test getting structure for custom series."""
        urls, structure = get_expected_structure(AstrometryIndexSeries.SERIES_CUSTOM)
        assert urls == []
        assert structure == {}

    def test_unknown_series(self):
        """Test that unknown series raises error."""
        with pytest.raises(AttributeError):
            get_expected_structure("unknown_series")


class TestExamineIndices:
    """Tests for the examine_indices function."""

    def test_nonexistent_path(self):
        """Test examining a nonexistent path."""
        result = examine_indices(
            Path("/nonexistent/path"),
            AstrometryIndexSeries.SERIES_5200_LITE,
        )
        assert result is False

    def test_empty_directory(self):
        """Test examining an empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = examine_indices(
                Path(tmpdir),
                AstrometryIndexSeries.SERIES_5200_LITE,
            )
            assert result is False

    def test_custom_series_always_valid(self):
        """Test that custom series always returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = examine_indices(
                Path(tmpdir),
                AstrometryIndexSeries.SERIES_CUSTOM,
            )
            assert result is True

    @pytest.mark.skipif(
        not Path("/stars/data/share/5000/5200-LITE").exists(),
        reason="5200-LITE indices not available",
    )
    def test_valid_indices(self):
        """Test examining valid indices (requires indices to be present)."""
        result = examine_indices(
            Path("/stars/data/share/5000/5200-LITE"),
            AstrometryIndexSeries.SERIES_5200_LITE,
        )
        assert result is True


class TestScalesForFov:
    """Tests for the scales_for_fov function."""

    def test_narrow_fov(self):
        """Test scales for a narrow 0.1 degree FOV."""
        scales = scales_for_fov(0.1)
        # 0.1 deg = 6 arcmin, 10% = 0.6 arcmin
        # Should include small scales only
        assert 0 in scales
        assert 1 in scales
        # Should not include large scales
        assert 10 not in scales
        assert 19 not in scales

    def test_one_degree_fov(self):
        """Test scales for a 1 degree FOV."""
        scales = scales_for_fov(1.0)
        # 1 deg = 60 arcmin, 10% = 6 arcmin
        # Scales with max > 6 and min < 60
        assert 3 in scales  # 5.66-8.0
        assert 9 in scales  # 45.25-64.0
        # Scale 10 has min=64 > 60 arcmin
        assert 10 not in scales

    def test_two_degree_fov(self):
        """Test scales for a 2 degree FOV."""
        scales = scales_for_fov(2.0)
        # 2 deg = 120 arcmin, 10% = 12 arcmin
        assert 5 in scales  # 11.31-16.0
        assert 11 in scales  # 90.51-128.0
        assert 12 not in scales  # 128.0 min >= 120

    def test_wide_fov(self):
        """Test scales for a wide 10 degree FOV."""
        scales = scales_for_fov(10.0)
        # 10 deg = 600 arcmin, 10% = 60 arcmin
        assert 9 in scales  # 45.25-64.0 (max > 60)
        assert 16 in scales  # 512.0-724.08 (min < 600)

    def test_returns_sorted(self):
        """Test that scales are returned sorted."""
        scales = scales_for_fov(1.0)
        assert scales == sorted(scales)

    def test_very_narrow(self):
        """Test a very narrow FOV includes scale 0."""
        # 0.05 deg = 3 arcmin; 10% = 0.3 arcmin, 100% = 3 arcmin
        # Scale 0 (2.0-2.83) overlaps: max 2.83 > 0.3 and min 2.0 < 3.0
        scales = scales_for_fov(0.05)
        assert 0 in scales


class TestFilterStructureByScales:
    """Tests for the filter_structure_by_scales function."""

    def test_filter_5200_lite(self):
        """Test filtering 5200_LITE structure by scales."""
        scales = [0, 1]
        filtered = filter_structure_by_scales(ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE, scales)
        # Should only have 5200 and 5201 files (scale 0 and 1)
        for filename in filtered:
            assert filename.startswith("index-5200-") or filename.startswith("index-5201-")
        # Original has scales 0-6, filtered should be smaller
        assert len(filtered) < len(ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE)

    def test_filter_4100(self):
        """Test filtering 4100 structure by scales."""
        scales = [7, 8]
        filtered = filter_structure_by_scales(ASTROMETRY_4100_EXPECTED_STRUCTURE, scales)
        # Should only have 4107 and 4108 files
        assert "index-4107.fits" in filtered
        assert "index-4108.fits" in filtered
        assert len(filtered) == 2

    def test_filter_no_matching_scales(self):
        """Test filtering with no matching scales returns empty."""
        scales = [19]  # 4100 only has 7-19, but 5200_LITE has 0-6
        filtered = filter_structure_by_scales(ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE, scales)
        assert len(filtered) == 0

    def test_filter_all_scales(self):
        """Test filtering with all scales returns all files."""
        scales = list(range(20))
        filtered = filter_structure_by_scales(
            ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE, scales
        )
        assert len(filtered) == len(ASTROMETRY_5200_LITE_EXPECTED_STRUCTURE)
