"""Versioned, non-operational TAF configuration comparisons.

This module deliberately keeps replay-tested rain wording policies separate
from the live, event-adaptive TAF path.  It creates comparable guidance for
review only; callers must never use its output to replace the selected TAF.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from src.taf_core import RainConfig
from src.tafor_generator import generate_tafor


SHADOW_SCHEMA_VERSION = "shadow-taf-guidance-v1"
SHADOW_MODE = "shadow_only"
SHADOW_ISSUANCES = ("2300", "0500", "1100", "1700")


@dataclass(frozen=True)
class ShadowTAFConfig:
    """A replay-tested rain/TS wording policy retained for shadow review."""

    key: str
    version: str
    label: str
    description: str
    wet_model_probability_threshold: float
    min_probability_agreement: float
    min_amount_agreement: float
    max_bridge_gap_hours: int
    ts_policy: str
    min_rain_signal_hours: int = 1
    becmg_min_rain_hours: int = 1
    becmg_min_agreement: float = 0.10
    evidence_scope: str = "Jan-Jun 2026 historical replay; comparison only"

    def generator_config(self):
        """Return an isolated RainConfig subclass for this exact policy."""
        return type(
            f"ShadowTAF_{self.key}_{self.version.replace('-', '_')}",
            (RainConfig,),
            {
                "WET_MODEL_PROBABILITY_THR": self.wet_model_probability_threshold,
                "MIN_PROBABILITY_AGREEMENT": self.min_probability_agreement,
                "MIN_RAIN_SIGNAL_AGREEMENT": self.min_amount_agreement,
                "MAX_BRIDGE_GAP": self.max_bridge_gap_hours,
                "MIN_RAIN_SIGNAL_HOURS": self.min_rain_signal_hours,
                "TS_POLICY": self.ts_policy,
                "BECMG_MIN_RAIN_HOURS": self.becmg_min_rain_hours,
                "BECMG_MIN_AGREEMENT": self.becmg_min_agreement,
            },
        )

    def metadata(self) -> dict[str, Any]:
        """JSON-safe configuration and non-promotion contract."""
        return {
            **asdict(self),
            "mode": SHADOW_MODE,
            "promotion_status": "not_promoted",
            "selected_operational_impact": "none",
            "comparison_target": "event-adaptive guidance in tafor_intel.json",
            "event_calibration": "preserved except for this configuration's explicit rain/TS overrides",
        }


# These three policies are the common candidates used in the Jan-Jun replay.
# Keep their identifiers stable so later verification can reference them.
SHADOW_TAF_CONFIGS = (
    ShadowTAFConfig(
        key="control_current",
        version="rain-control-v1",
        label="Control current",
        description=(
            "Frozen replay baseline: 40% wet-model probability, no minimum "
            "agreement gate, and a two-hour rain bridge. This is a comparison "
            "baseline, not a claim that it exactly matches live event adaptation."
        ),
        wet_model_probability_threshold=40.0,
        min_probability_agreement=0.0,
        min_amount_agreement=0.0,
        max_bridge_gap_hours=2,
        ts_policy="broad_current",
    ),
    ShadowTAFConfig(
        key="rain_50_20",
        version="rain-50-20-v1",
        label="Rain 50/20",
        description=(
            "Balanced candidate: require 50% weighted wet-model probability "
            "and 20% spread-adjusted agreement; disable rain-gap bridging."
        ),
        wet_model_probability_threshold=50.0,
        min_probability_agreement=0.20,
        min_amount_agreement=0.0,
        max_bridge_gap_hours=0,
        ts_policy="broad_current",
    ),
    ShadowTAFConfig(
        key="rain_50_20_becmg3",
        version="rain-50-20-becmg3-v1",
        label="Rain 50/20 with BECMG guard",
        description=(
            "Rain 50/20 plus BECMG wording only after three rainy hours and "
            "25% spread-adjusted agreement; weaker events remain temporary or "
            "probability wording."
        ),
        wet_model_probability_threshold=50.0,
        min_probability_agreement=0.20,
        min_amount_agreement=0.0,
        max_bridge_gap_hours=0,
        ts_policy="broad_current",
        becmg_min_rain_hours=3,
        becmg_min_agreement=0.25,
    ),
)


def build_shadow_taf_payload(
    consensus,
    model_data: Mapping[str, Any],
    qm_rain_data: Mapping[str, Any],
    model_weights: Mapping[str, Any],
    event_weight_diagnostics: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    configs: tuple[ShadowTAFConfig, ...] = SHADOW_TAF_CONFIGS,
    issuances: tuple[str, ...] = SHADOW_ISSUANCES,
    generate_fn: Callable[..., dict] = generate_tafor,
) -> dict[str, Any]:
    """Generate review-only TAF alternatives without selecting or promoting one."""
    generated_at = generated_at or datetime.now(timezone.utc)
    issuance_payload: dict[str, dict[str, Any]] = {}

    for issuance in issuances:
        alternatives: dict[str, dict[str, Any]] = {}
        for config in configs:
            taf = generate_fn(
                consensus,
                model_data,
                qm_rain_data,
                model_weights,
                target_issuance=issuance,
                event_weight_diagnostics=event_weight_diagnostics,
                experimental_taf_config=config.generator_config(),
            )
            alternatives[config.key] = {
                "config": config.metadata(),
                "guidance": taf or None,
                "provenance": {
                    "mode": SHADOW_MODE,
                    "selected_operational_impact": "none",
                    "comparison_target": "event-adaptive guidance in tafor_intel.json",
                    "event_calibration": "preserved except for this configuration's explicit rain/TS overrides",
                    "issued_for": f"{issuance}Z",
                },
            }
        issuance_payload[issuance] = alternatives

    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "status": SHADOW_MODE,
        "generated_at_utc": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "message": (
            "Shadow configuration guidance is for comparison and verification only. "
            "It does not alter the selected operational TAF guidance."
        ),
        "config_order": [config.key for config in configs],
        "issuances": issuance_payload,
    }
