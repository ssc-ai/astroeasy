"""Sensor profile: measured geometry + gate thresholds bridging characterization
(one-time blind solves) to every subsequent fast solve.

See docs/catalog-native-solving-roadmap.md §5. Produced by
:func:`astroeasy.cascade.characterize.characterize_sensor`, consumed by
:func:`astroeasy.cascade.solve`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GateThresholds:
    """Acceptance-gate thresholds (§4). Tuned per sensor at characterization."""

    min_matches: int = 8
    # rms guards fit quality, not correctness (log_odds is the wrong-solve
    # discriminator); large frames with distortion sit at ~3-5px pre-SIP-refit.
    max_rms_px: float = 5.0
    min_log_odds: float = 10.0
    match_tolerance_px: float = 8.0
    # Faint limit for the gate/refine catalog cone; defaults to the profile's
    # measured depth (or 16.0 if unmeasured) when None.
    catalog_faint_limit: float | None = None


@dataclass
class SensorProfile:
    """Measured sensor geometry + solving artifacts + gate thresholds.

    Every field except ``sensor_id`` is optional: tiers short-circuit on missing
    capability (no tetra3 DB -> skip T1; no scale -> only astrometry.net tiers).
    """

    sensor_id: str
    pixel_scale_arcsec: float | None = None     # iFoV (arcsec/px)
    fov_degrees: float | None = None            # measured field width
    scale_bounds_degrees: tuple[float, float] | None = None  # (lo, hi) field width
    rotation_prior_deg: float | None = None     # None = per-frame rotation unknown
    parity: int = 1                             # +1 = normal, -1 = mirrored (sign of det CD)
    sip_order: int = 2                          # SIP order for the native refine fit
    mag_depth_g: float | None = None            # measured limiting G of the detections
    tetra3_db_path: str | None = None           # T1 pattern DB (.npz)
    custom_index_path: str | None = None        # T2 custom astrometry.net index dir
    gate: GateThresholds = field(default_factory=GateThresholds)

    def __post_init__(self):
        if isinstance(self.gate, dict):
            self.gate = GateThresholds(**self.gate)
        if self.scale_bounds_degrees is not None:
            self.scale_bounds_degrees = tuple(self.scale_bounds_degrees)

    @property
    def gate_faint_limit(self) -> float:
        """Catalog faint limit the gate/refine cone should use."""
        if self.gate.catalog_faint_limit is not None:
            return self.gate.catalog_faint_limit
        if self.mag_depth_g is not None:
            return self.mag_depth_g
        return 16.0

    def fov_estimate(self) -> float | None:
        """Best available field-width estimate in degrees."""
        if self.fov_degrees is not None:
            return self.fov_degrees
        if self.scale_bounds_degrees is not None:
            lo, hi = self.scale_bounds_degrees
            return (lo + hi) / 2.0
        return None

    def fov_max_error(self) -> float | None:
        """Half-width of the field-width uncertainty in degrees (for tetra3)."""
        if self.fov_degrees is not None:
            return max(0.05, 0.02 * self.fov_degrees)
        if self.scale_bounds_degrees is not None:
            lo, hi = self.scale_bounds_degrees
            return max(0.05, (hi - lo) / 2.0)
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.scale_bounds_degrees is not None:
            d["scale_bounds_degrees"] = list(self.scale_bounds_degrees)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorProfile:
        return cls(**data)

    def to_yaml(self, path: Path | str) -> None:
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, path: Path | str) -> SensorProfile:
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))
