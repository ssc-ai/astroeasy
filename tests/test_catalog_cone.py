"""Catalog cones near the pole (astroeasy.cascade.catalog.query_cone)."""

import json

import numpy as np
import pytest

from astroeasy.cascade.catalog import _separation_deg, query_cone
from astroeasy.catalog.mirror import MIRROR_DTYPE, _ra_subranges, load_mirror_index


@pytest.fixture
def polar_mirror(tmp_path):
    """One tile: a 2-degree grid of stars over Dec 50..89, every RA."""
    ra, dec = np.meshgrid(np.arange(0.0, 360.0, 2.0), np.arange(50.0, 90.0, 2.0) + 0.5)
    n = ra.size
    arr = np.zeros(n, dtype=MIRROR_DTYPE)
    arr["source_id"] = np.arange(n)
    arr["ra"], arr["dec"] = ra.ravel(), dec.ravel()
    arr["g"] = 8.0
    arr.tofile(tmp_path / "tile.bin")
    index = {"tiles": {"0": {"file": "tile.bin", "ra_min": 0.0, "ra_max": 360.0, "dec_min": 50.0, "dec_max": 90.0}}}
    (tmp_path / "index.json").write_text(json.dumps(index))
    load_mirror_index.cache_clear()
    return str(tmp_path), arr


@pytest.mark.parametrize(
    ("ra0", "dec0", "radius"),
    [
        (47.5, 74.3, 12.5),  # padded box > 360 deg wide, below the 88 deg rule (returned 0 stars)
        (200.0, 70.0, 12.5),
        (10.0, 80.0, 12.5),  # cone over the pole
        (350.0, 60.0, 8.0),  # straddles RA 0
    ],
)
def test_cone_matches_brute_force(polar_mirror, ra0, dec0, radius):
    mirror, arr = polar_mirror
    cone = query_cone(mirror, ra0, dec0, radius, faint_limit=12.0)
    want = set(arr["source_id"][_separation_deg(ra0, dec0, arr["ra"], arr["dec"]) <= radius])
    assert want and set(cone.source_id) == want


def test_wide_ra_box_is_the_whole_ring():
    assert _ra_subranges(-176.0, 271.0) == [(0.0, 360.0)]
    assert _ra_subranges(-10.0, 350.0) == [(0.0, 360.0)]
    assert _ra_subranges(350.0, 370.0) == [(350.0, 360.0), (0.0, 10.0)]


def test_cone_reuse_uses_true_separation(polar_mirror):
    mirror, _arr = polar_mirror
    cone = query_cone(mirror, 0.0, 85.0, 10.0, faint_limit=12.0)
    # 180 deg of RA apart at Dec 85 is 10 deg of sky: a 1 deg cone there is not inside
    assert not cone.covers(180.0, 85.0, 1.0, 12.0)
    assert cone.covers(0.0, 86.0, 1.0, 12.0)
