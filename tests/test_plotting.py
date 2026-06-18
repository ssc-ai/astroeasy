"""Tests for the plotting module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astroeasy.plotting.normalization import zscale
from astroeasy.plotting.images import (
    _font_size,
    _format_ra,
    _format_dec,
    _calculate_line_angle,
    plot_solved_field,
)


class TestZscale:
    """Tests for zscale normalization."""

    def test_basic_array(self):
        """Test zscale on a basic array."""
        data = np.random.randn(100, 100) * 100 + 1000
        result = zscale(data)
        assert result.shape == data.shape
        assert result.dtype == np.float32
        # Result should be normalized to roughly [0, 1]
        assert np.min(result) >= 0
        assert np.max(result) <= 1

    def test_handles_nan(self):
        """Test that zscale handles NaN values."""
        data = np.random.randn(50, 50) * 100 + 1000
        data[10, 10] = np.nan
        data[20, 20] = np.nan
        result = zscale(data)
        assert result.shape == data.shape
        assert np.all(np.isfinite(result))

    def test_handles_inf(self):
        """Test that zscale handles Inf values."""
        data = np.random.randn(50, 50) * 100 + 1000
        data[10, 10] = np.inf
        data[20, 20] = -np.inf
        result = zscale(data)
        assert result.shape == data.shape
        assert np.all(np.isfinite(result))

    def test_all_nan_returns_zeros(self):
        """Test that all-NaN input returns zeros."""
        data = np.full((10, 10), np.nan)
        result = zscale(data)
        assert np.all(result == 0)

    def test_contrast_parameter(self):
        """Test that contrast parameter affects output."""
        # Seed locally and use a realistic frame (faint background + a few
        # bright sources). Unseeded pure Gaussian noise occasionally makes the
        # two contrast stretches compare equal (flaky); a heavy-tailed field
        # makes the contrast dependence deterministic.
        rng = np.random.default_rng(0)
        data = rng.standard_normal((50, 50)).astype(np.float32) * 10 + 100
        for r, c, v in [(5, 5, 3000), (40, 12, 5000), (25, 30, 8000), (10, 45, 2000)]:
            data[r, c] = v
        result1 = zscale(data, contrast=0.1)
        result2 = zscale(data, contrast=0.5)
        # Different contrast should give different results
        assert not np.allclose(result1, result2)


class TestFontSize:
    """Tests for font size calculation."""

    def test_small_image(self):
        """Test font size for small image."""
        img = np.zeros((100, 100))
        fs = _font_size(img)
        assert fs == 6  # Minimum font size

    def test_large_image(self):
        """Test font size for large image."""
        img = np.zeros((1000, 1000))
        fs = _font_size(img)
        assert fs == 10  # 1000 * 0.01

    def test_rectangular_image(self):
        """Test font size uses minimum dimension, capped at min 6."""
        img = np.zeros((500, 2000))
        fs = _font_size(img)
        # min(500, 2000) * 0.01 = 5, but max(6, 5) = 6
        assert fs == 6


class TestFormatRA:
    """Tests for RA formatting."""

    def test_format_zero(self):
        """Test formatting RA = 0."""
        result = _format_ra(0.0)
        assert "h" in result
        assert "m" in result
        assert "s" in result

    def test_format_180(self):
        """Test formatting RA = 180 degrees."""
        result = _format_ra(180.0)
        assert "12h" in result  # 180 deg = 12 hours

    def test_format_90(self):
        """Test formatting RA = 90 degrees."""
        result = _format_ra(90.0)
        assert "06h" in result  # 90 deg = 6 hours


class TestFormatDec:
    """Tests for Dec formatting."""

    def test_format_zero(self):
        """Test formatting Dec = 0."""
        result = _format_dec(0.0)
        assert "°" in result or "'" in result

    def test_format_positive(self):
        """Test formatting positive Dec."""
        result = _format_dec(45.0)
        assert "+" in result
        assert "45" in result

    def test_format_negative(self):
        """Test formatting negative Dec."""
        result = _format_dec(-30.0)
        assert "-" in result
        assert "30" in result


class TestCalculateLineAngle:
    """Tests for line angle calculation."""

    def test_horizontal_line(self):
        """Test angle of horizontal line."""
        x = np.array([0, 10, 20, 30])
        y = np.array([5, 5, 5, 5])
        angle = _calculate_line_angle(x, y, 15, 5)
        assert abs(angle) < 5  # Should be close to 0

    def test_vertical_line(self):
        """Test angle of vertical line."""
        x = np.array([5, 5, 5, 5])
        y = np.array([0, 10, 20, 30])
        angle = _calculate_line_angle(x, y, 5, 15)
        assert abs(abs(angle) - 90) < 5  # Should be close to 90 or -90

    def test_diagonal_line(self):
        """Test angle of 45-degree line."""
        x = np.array([0, 10, 20, 30])
        y = np.array([0, 10, 20, 30])
        angle = _calculate_line_angle(x, y, 15, 15)
        assert abs(angle - 45) < 5  # Should be close to 45

    def test_single_point(self):
        """Test with single point returns 0."""
        x = np.array([10])
        y = np.array([10])
        angle = _calculate_line_angle(x, y, 10, 10)
        assert angle == 0.0


class TestPlotSolvedField:
    """Tests for plot_solved_field function."""

    @pytest.fixture
    def sample_fits_file(self, tmp_path):
        """Create a sample FITS file for testing."""
        # Create simple image data
        data = np.random.randn(100, 100) * 100 + 1000
        data = data.astype(np.float32)

        # Create a simple WCS header
        header = fits.Header()
        header["SIMPLE"] = True
        header["BITPIX"] = -32
        header["NAXIS"] = 2
        header["NAXIS1"] = 100
        header["NAXIS2"] = 100
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRPIX1"] = 50.0
        header["CRPIX2"] = 50.0
        header["CRVAL1"] = 180.0
        header["CRVAL2"] = 45.0
        header["CDELT1"] = -0.001
        header["CDELT2"] = 0.001
        header["CUNIT1"] = "deg"
        header["CUNIT2"] = "deg"

        # Write FITS file
        fits_path = tmp_path / "test_image.fits"
        hdu = fits.PrimaryHDU(data, header=header)
        hdu.writeto(fits_path)
        return fits_path

    def test_basic_plot(self, sample_fits_file, tmp_path):
        """Test basic plot generation."""
        output_path = tmp_path / "output.png"
        plot_solved_field(
            image_path=sample_fits_file,
            output_path=output_path,
            show_grid=False,  # Skip grid for speed
        )
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_plot_with_grid(self, sample_fits_file, tmp_path):
        """Test plot with WCS grid."""
        output_path = tmp_path / "output_grid.png"
        plot_solved_field(
            image_path=sample_fits_file,
            output_path=output_path,
            show_grid=True,
        )
        assert output_path.exists()

    def test_plot_returns_fig_ax(self, sample_fits_file):
        """Test that plot returns fig, ax when no output path."""
        result = plot_solved_field(
            image_path=sample_fits_file,
            output_path=None,
            show_grid=False,
        )
        assert result is not None
        fig, ax = result
        assert fig is not None
        assert ax is not None

    def test_plot_png_output(self, sample_fits_file, tmp_path):
        """Test PNG output format explicitly."""
        output_path = tmp_path / "output_explicit.png"
        plot_solved_field(
            image_path=sample_fits_file,
            output_path=output_path,
            show_grid=False,
        )
        assert output_path.exists()

    def test_plot_with_title(self, sample_fits_file, tmp_path):
        """Test plot with title."""
        output_path = tmp_path / "output_title.png"
        plot_solved_field(
            image_path=sample_fits_file,
            output_path=output_path,
            show_grid=False,
            title="Test Field",
        )
        assert output_path.exists()

    def test_plot_with_catalog_stars(self, sample_fits_file, tmp_path):
        """Test plot with catalog stars overlay."""
        from astroeasy.catalog.gaia import CatalogStar

        catalog_stars = [
            CatalogStar(ra=180.0, dec=45.0, magnitude=10.0),
            CatalogStar(ra=180.01, dec=45.01, magnitude=12.0),
        ]

        output_path = tmp_path / "output_catalog.png"
        plot_solved_field(
            image_path=sample_fits_file,
            output_path=output_path,
            show_grid=False,
            catalog_stars=catalog_stars,
        )
        assert output_path.exists()

    def test_plot_with_matched_stars(self, sample_fits_file, tmp_path):
        """Test plot with matched stars overlay."""
        from astroeasy.models import MatchedStar

        matched_stars = [
            MatchedStar(ra=180.0, dec=45.0, x=50, y=50),
            MatchedStar(ra=180.01, dec=45.01, x=60, y=60),
        ]

        output_path = tmp_path / "output_matched.png"
        plot_solved_field(
            image_path=sample_fits_file,
            output_path=output_path,
            show_grid=False,
            matched_stars=matched_stars,
        )
        assert output_path.exists()
