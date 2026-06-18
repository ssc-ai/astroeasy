"""The escalation cascade: one solve() running cheapest-first over the tiers.

T0 (native refine from a prior) → T1 (tetra3 pattern match) → T3/T4
(astrometry.net via the existing, untouched ``astroeasy.solve_field`` — hinted
when a boresight is present, blind otherwise). Every native candidate must
clear the acceptance gate (gate.py); a rung that can't REJECTS and the
cascade escalates. astrometry.net results are accepted on its own verification
(it is the deep backstop — the cascade must never fail a frame it would have
solved) and gate-scored for telemetry only.

Tiers short-circuit on missing capability: no prior → T0 skipped; no catalog
mirror → all native tiers skipped; no tetra3 DB → T1 skipped; no dotnet
config → astrometry.net skipped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from astroeasy.cascade import tier0, tier1
from astroeasy.cascade.catalog import Cone, query_cone
from astroeasy.cascade.gate import GateResult, score_wcs
from astroeasy.cascade.profile import SensorProfile
from astroeasy.cascade.wcsfit import (
    awcs_to_wcsresult,
    detections_to_arrays,
    matched_stars_from_gate,
)
from astroeasy.config import AstrometryConfig
from astroeasy.models import Detection, ImageMetadata, SolveResult, WCSResult, WCSStatus

logger = logging.getLogger(__name__)

DEFAULT_TIERS = ("T0", "T1", "T3")


@dataclass
class TierAttempt:
    """One rung's outcome, for telemetry/shadow comparison."""

    tier: str
    accepted: bool
    status: str
    duration_ms: float
    gate: GateResult | None = None


@dataclass
class CascadeResult:
    """Cascade outcome: a standard SolveResult plus per-tier telemetry."""

    solve: SolveResult
    tier: str | None                      # winning tier, None if all failed
    attempts: list[TierAttempt] = field(default_factory=list)


def solve(
    detections: list[Detection],
    metadata: ImageMetadata,
    *,
    profile: SensorProfile,
    mirror_dir: str | None = None,
    dotnet_config: AstrometryConfig | None = None,
    prior_wcs: WCSResult | None = None,
    tiers: tuple[str, ...] = DEFAULT_TIERS,
) -> CascadeResult:
    """Run the escalation cascade; return on the first accepted solve.

    Args:
        detections: Detected sources (x, y FITS-convention, flux).
        metadata: Image size + optional boresight hint.
        profile: Sensor profile (geometry, artifacts, gate thresholds).
        mirror_dir: Local Gaia mirror for the native tiers + gate. None
            disables T0/T1.
        dotnet_config: AstrometryConfig for the astrometry.net backstop. None
            disables T3/T4.
        prior_wcs: Previous-frame / propagated WCS (enables the fast T0 path).
        tiers: Which rungs to run, in order. "T3" = astrometry.net (hinted when
            a boresight exists — i.e. T4/blind when it doesn't).
    """
    attempts: list[TierAttempt] = []
    det_x, det_y = detections_to_arrays(detections)
    width, height = metadata.width, metadata.height
    fov = profile.fov_estimate()
    pixel_scale = profile.pixel_scale_arcsec or (fov * 3600.0 / width if fov else None)

    cones: list[Cone] = []

    def get_cone(ra0: float, dec0: float, radius: float) -> Cone:
        faint = profile.gate_faint_limit
        for c in cones:
            if c.covers(ra0, dec0, radius, faint):
                return c
        c = query_cone(mirror_dir, ra0, dec0, radius, faint)
        cones.append(c)
        return c

    def cone_radius() -> float:
        # Half-diagonal plus margin for boresight/pointing error.
        return max(0.75 * fov, fov / 2.0 + 0.35) if fov else 1.5

    def finish_native(tier_name: str, awcs, cone: Cone, g: GateResult) -> CascadeResult:
        wcs_result = awcs_to_wcsresult(awcs, width, height)
        sr = SolveResult(
            success=True, status=WCSStatus.SUCCESS, wcs=wcs_result,
            matched_stars=matched_stars_from_gate(cone, g, det_x, det_y),
            detections=detections, image_metadata=metadata,
        )
        return CascadeResult(solve=sr, tier=tier_name, attempts=attempts)

    def refine_and_gate(tier_name: str, candidate, cone: Cone, t0: float):
        """Gate a tier candidate; record the attempt; return a CascadeResult on accept."""
        if not candidate.ok:
            attempts.append(TierAttempt(tier_name, False, candidate.status,
                                        (perf_counter() - t0) * 1e3))
            logger.info("cascade %s: %s -> escalate", tier_name, candidate.status)
            return None
        g = score_wcs(candidate.awcs, det_x, det_y, cone, width, height, profile.gate)
        attempts.append(TierAttempt(tier_name, g.accepted, candidate.status,
                                    (perf_counter() - t0) * 1e3, gate=g))
        if not g.accepted:
            logger.info("cascade %s: candidate REJECTED by gate (%s) -> escalate",
                        tier_name, g.reason)
            return None
        logger.info("cascade %s: ACCEPT (%d matches, rms %.2fpx, log-odds %.0f) in %.0fms",
                    tier_name, g.n_matches, g.rms_px, g.log_odds, attempts[-1].duration_ms)
        return finish_native(tier_name, candidate.awcs, cone, g)

    native_possible = mirror_dir is not None
    if native_possible and not os.path.isfile(os.path.join(mirror_dir, "index.json")):
        # e.g. the mirror lives on an external drive that isn't mounted right
        # now — degrade to the backstop instead of crashing the pipeline.
        logger.warning("cascade: mirror_dir %s has no index.json (unmounted?) — "
                       "native tiers (T0/T1) skipped", mirror_dir)
        native_possible = False
    if not native_possible and any(t in tiers for t in ("T0", "T1")):
        logger.info("cascade: no usable mirror_dir — native tiers (T0/T1) skipped")

    def _run_t0(t0: float) -> CascadeResult | None:
        """T0: refine from a full prior WCS, or boresight+scale constrained."""
        if prior_wcs is not None:
            cone = get_cone(prior_wcs.center_ra, prior_wcs.center_dec, cone_radius())
            cand = tier0.refine_wcs(
                det_x, det_y, prior_wcs.to_astropy_wcs(), cone, width, height,
                sip_order=profile.sip_order,
                min_matches=profile.gate.min_matches,
            )
            return refine_and_gate("T0", cand, cone, t0)
        if (metadata.boresight_ra is not None and metadata.boresight_dec is not None
                and pixel_scale is not None):
            cone = get_cone(metadata.boresight_ra, metadata.boresight_dec, cone_radius())
            rolls = None
            if profile.rotation_prior_deg is not None:
                rolls = np.arange(profile.rotation_prior_deg - 10,
                                  profile.rotation_prior_deg + 10, 1.0)
            # Both parities always: the profile's measured parity is CD-matrix
            # telemetry whose sign convention differs from the matcher's eta
            # flip — searching both costs one extra coarse pass and removes a
            # silent-mismatch failure mode.
            cand = tier0.solve_constrained(
                det_x, det_y, cone, width, height,
                scale_asec_per_px=pixel_scale, rolls_deg=rolls,
            )
            if cand.ok:
                cand = tier0.refine_wcs(det_x, det_y, cand.awcs, cone, width, height,
                                        sip_order=profile.sip_order,
                                        min_matches=profile.gate.min_matches)
            return refine_and_gate("T0", cand, cone, t0)
        logger.debug("cascade T0: no usable prior — skipped")
        return None

    def _run_t1(t0: float) -> CascadeResult | None:
        """T1: tetra3 lost-in-space lock, then constrained re-orient + refine."""
        lock = tier1.solve_tetra3(
            det_x, det_y, width, height, profile.tetra3_db_path,
            fov_estimate=fov, fov_max_error=profile.fov_max_error(),
        )
        if not lock.ok:
            attempts.append(TierAttempt("T1", False, lock.status,
                                        (perf_counter() - t0) * 1e3))
            logger.info("cascade T1: tetra3 %s -> escalate", lock.status)
            return None
        # tetra3's center is precise (~arcsec); re-derive roll/parity with the
        # constrained matcher in a tight cone, then refine + gate.
        lock_fov = lock.fov_deg or fov
        lock_scale = (lock_fov * 3600.0 / width) if lock_fov else pixel_scale
        cone = get_cone(lock.ra, lock.dec, cone_radius())
        cand = tier0.solve_constrained(
            det_x, det_y,
            Cone(ra0=lock.ra, dec0=lock.dec, radius_deg=cone.radius_deg,
                 faint_limit=cone.faint_limit, ra=cone.ra, dec=cone.dec,
                 g=cone.g, source_id=cone.source_id),
            width, height, scale_asec_per_px=lock_scale,
        )
        if cand.ok:
            cand = tier0.refine_wcs(det_x, det_y, cand.awcs, cone, width, height,
                                    sip_order=profile.sip_order,
                                    min_matches=profile.gate.min_matches)
        return refine_and_gate("T1", cand, cone, t0)

    native_runners = {"T0": _run_t0, "T1": _run_t1}

    for tier_name in tiers:
        # ---------------- T0/T1: native tiers ----------------
        if tier_name in native_runners and native_possible and (
                tier_name != "T1" or profile.tetra3_db_path):
            t0 = perf_counter()
            try:
                result = native_runners[tier_name](t0)
            except Exception as e:  # noqa: BLE001 — a native-tier crash must escalate, not fail the frame
                attempts.append(TierAttempt(tier_name, False, "ERROR",
                                            (perf_counter() - t0) * 1e3))
                logger.warning("cascade %s errored (%s) -> escalate", tier_name, e)
                result = None
            if result:
                return result

        # ---------------- T3/T4: astrometry.net backstop ----------------
        elif tier_name in ("T3", "T4") and dotnet_config is not None:
            from astroeasy.runner import solve_field

            t0 = perf_counter()
            md, prior = metadata, prior_wcs
            if tier_name == "T4":
                md = ImageMetadata(width=width, height=height)  # strip hints: true blind
                prior = None
            res = solve_field(detections, md, dotnet_config, existing_wcs=prior)
            duration = (perf_counter() - t0) * 1e3
            gate_score = None
            if res.success and native_possible:
                try:
                    cone = get_cone(res.wcs.center_ra, res.wcs.center_dec, cone_radius())
                    gate_score = score_wcs(res.wcs.to_astropy_wcs(), det_x, det_y,
                                           cone, width, height, profile.gate)
                    if not gate_score.accepted:
                        logger.warning(
                            "cascade %s: gate disagrees with astrometry.net (%s) — "
                            "accepting astrometry.net, review gate thresholds",
                            tier_name, gate_score.reason,
                        )
                except Exception as e:  # noqa: BLE001 — telemetry only, never block the backstop
                    logger.debug("gate telemetry on %s failed: %s", tier_name, e)
            attempts.append(TierAttempt(tier_name, res.success, str(res.status.value),
                                        duration, gate=gate_score))
            if res.success:
                logger.info("cascade %s: astrometry.net SUCCESS in %.0fms", tier_name, duration)
                return CascadeResult(solve=res, tier=tier_name, attempts=attempts)
            logger.info("cascade %s: astrometry.net %s -> escalate", tier_name, res.status)

    logger.warning("cascade: all tiers exhausted without an accepted solve "
                   "(attempts: %s)", [(a.tier, a.status) for a in attempts])
    failed = SolveResult(success=False, status=WCSStatus.FAILED, wcs=None,
                         matched_stars=[], detections=detections, image_metadata=metadata)
    return CascadeResult(solve=failed, tier=None, attempts=attempts)
