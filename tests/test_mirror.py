"""Tests for the local Gaia mirror reader (astroeasy.catalog.mirror)."""

import json

import numpy as np
import pytest

from astroeasy.catalog import query_gaia_field
from astroeasy.catalog.mirror import (
    MIRROR_DTYPE,
    _ra_subranges,
    load_mirror_index,
    query_gaia_field_local,
    query_mirror_box,
)


def _write_tile(path, rows):
    """rows: list of (source_id, ra, dec, g, bp, rp, pmra, pmdec)."""
    arr = np.array(rows, dtype=MIRROR_DTYPE)
    arr.tofile(path)
    return arr


@pytest.fixture
def mirror_dir(tmp_path):
    """A two-tile mirror: tile A near RA=10, tile B straddling the RA=0 seam."""
    tile_a = [
        (1, 10.0, 5.0, 8.0, 8.5, 7.5, 1.0, -1.0),
        (2, 11.0, 5.5, 12.0, 12.5, 11.5, 0.0, 0.0),
        (3, 12.0, 6.0, 16.0, np.nan, np.nan, np.nan, np.nan),
        (4, 10.5, 20.0, 9.0, 9.5, 8.5, 0.0, 0.0),  # outside dec box in tests
    ]
    tile_b = [
        (5, 359.5, 5.0, 10.0, 10.5, 9.5, 0.0, 0.0),
        (6, 0.5, 5.0, 11.0, 11.5, 10.5, 0.0, 0.0),
    ]
    _write_tile(tmp_path / "tile_a.bin", tile_a)
    _write_tile(tmp_path / "tile_b.bin", tile_b)
    index = {
        "tiles": {
            "a": {"file": "tile_a.bin", "ra_min": 9.0, "ra_max": 13.0, "dec_min": 4.0, "dec_max": 21.0},
            "b": {"file": "tile_b.bin", "ra_min": 0.0, "ra_max": 360.0, "dec_min": 4.0, "dec_max": 6.0},
        }
    }
    (tmp_path / "index.json").write_text(json.dumps(index))
    load_mirror_index.cache_clear()
    return str(tmp_path)


class TestRaSubranges:
    def test_no_wrap(self):
        assert _ra_subranges(10.0, 20.0) == [(10.0, 20.0)]

    def test_wraps_zero(self):
        assert _ra_subranges(350.0, 10.0) == [(350.0, 360.0), (0.0, 10.0)]

    def test_negative_input_normalized(self):
        assert _ra_subranges(-10.0, 10.0) == [(350.0, 360.0), (0.0, 10.0)]


class TestQueryMirrorBox:
    def test_basic_box(self, mirror_dir):
        rows = query_mirror_box(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir)
        assert sorted(rows["source_id"]) == [1, 2, 3]

    def test_wider_dec_box_includes(self, mirror_dir):
        rows = query_mirror_box(9.0, 13.0, 4.0, 30.0, mirror_dir=mirror_dir)
        assert 4 in rows["source_id"]

    def test_faint_limit(self, mirror_dir):
        rows = query_mirror_box(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir, faint_limit=12.5)
        assert sorted(rows["source_id"]) == [1, 2]

    def test_bright_limit(self, mirror_dir):
        rows = query_mirror_box(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir, bright_limit=10.0)
        assert sorted(rows["source_id"]) == [2, 3]

    def test_ra_seam_wrap(self, mirror_dir):
        rows = query_mirror_box(358.0, 2.0, 4.0, 6.0, mirror_dir=mirror_dir)
        assert sorted(rows["source_id"]) == [5, 6]

    def test_no_overlap_returns_empty(self, mirror_dir):
        rows = query_mirror_box(100.0, 110.0, -50.0, -40.0, mirror_dir=mirror_dir)
        assert len(rows) == 0
        assert rows.dtype == MIRROR_DTYPE

    def test_max_rows_keeps_brightest(self, mirror_dir):
        rows = query_mirror_box(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir, max_rows=2)
        assert sorted(rows["source_id"]) == [1, 2]  # G=8 and G=12 beat G=16

    def test_nan_g_excluded_by_mag_cut(self, mirror_dir):
        tile = [(7, 10.2, 5.2, np.nan, np.nan, np.nan, 0.0, 0.0)]
        _write_tile(f"{mirror_dir}/tile_c.bin", tile)
        index = json.loads(open(f"{mirror_dir}/index.json").read())
        index["tiles"]["c"] = {
            "file": "tile_c.bin", "ra_min": 9.0, "ra_max": 13.0, "dec_min": 4.0, "dec_max": 7.0,
        }
        open(f"{mirror_dir}/index.json", "w").write(json.dumps(index))
        load_mirror_index.cache_clear()

        rows = query_mirror_box(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir, faint_limit=20.0)
        assert 7 not in rows["source_id"]
        rows = query_mirror_box(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir)
        assert 7 in rows["source_id"]


class TestQueryGaiaFieldLocal:
    def test_returns_catalog_stars_brightest_first(self, mirror_dir):
        stars = query_gaia_field_local(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir)
        assert [s.source_id for s in stars] == ["1", "2", "3"]
        assert stars[0].ra == 10.0
        assert stars[0].dec == 5.0
        assert stars[0].magnitude == 8.0
        assert stars[0].catalog == "Gaia"

    def test_max_stars_cap(self, mirror_dir):
        stars = query_gaia_field_local(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir, max_stars=1)
        assert [s.source_id for s in stars] == ["1"]

    def test_dispatch_via_query_gaia_field(self, mirror_dir):
        stars = query_gaia_field(9.0, 13.0, 4.0, 7.0, mirror_dir=mirror_dir)
        assert [s.source_id for s in stars] == ["1", "2", "3"]
