"""astroeasy.cascade — catalog-native escalation solver (optional layer).

Additive to the core "astrometry.net made easy" API: nothing here changes
``astroeasy.solve_field``. Heavy deps (vendored tetra3's scipy/Pillow) load
lazily — install them with ``pip install astroeasy[cascade]``.

See docs/catalog-native-solving-roadmap.md.
"""

from astroeasy.cascade.characterize import CharacterizationResult, characterize_sensor
from astroeasy.cascade.gate import GateResult, score_wcs
from astroeasy.cascade.index_build import build_custom_index
from astroeasy.cascade.profile import GateThresholds, SensorProfile
from astroeasy.cascade.solve import CascadeResult, TierAttempt, solve
from astroeasy.cascade.tetra3db import build_tetra3_db, db_mag_for_fov

__all__ = [
    "CascadeResult",
    "CharacterizationResult",
    "GateResult",
    "GateThresholds",
    "SensorProfile",
    "TierAttempt",
    "build_custom_index",
    "build_tetra3_db",
    "characterize_sensor",
    "db_mag_for_fov",
    "score_wcs",
    "solve",
]
