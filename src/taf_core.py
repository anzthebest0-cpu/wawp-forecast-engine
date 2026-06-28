"""
taf_core.py - WAWP Pure-Function TAF Core  (P0-1 extraction)
=============================================================

Extracted from tafor_generator.py (P0-1 structural improvement).
Contains zero Streamlit code - safe to import from unit tests,
the verification loop (Phase 5), and any future CLI tooling.

Public API
----------
    RainConfig              - all rain-related threshold constants
    _build_change_groups()  - SOP-compliant change group builder
                              Returns (groups, warnings) tuple.
                              Caller is responsible for displaying
                              warnings (e.g. via st.sidebar.warning).

Design rule: this file MUST NOT import streamlit, pandas, os,
glob, or json.  Only: numpy, datetime, advanced_ensemble_weighter.
"""

from __future__ import annotations

import numpy as np
from datetime import datetime, timedelta

from src.advanced_ensemble_weighter import MODELS


# ==============================================================================
# RAIN THRESHOLD CONFIGURATION
# ==============================================================================

class RainConfig:
    """
    Single source of truth for all rain-related thresholds.

    Previously these values were scattered as bare literals in at least
    four separate locations (_build_change_groups, _build_rain_signal,
    calculate_shifter_intel, clim_sanity_check), making it easy to change
    one instance while missing another and silently breaking the system.

    Changing any value here propagates automatically to every function
    that references it.

    Thresholds
    ----------
    CONSENSUS_THR    Weighted ensemble mean must reach this to treat an
                     hour as rainy in consensus_truth["rain"] and rainy_sig.
                     Also used as the gate for base-state wx classification.

    VOTE_THR         Individual model bias-corrected rain must reach this
                     for the model to cast a vote in rain_agreement().
                     Higher than CONSENSUS_THR to filter noise-level signals
                     from individual models before they affect tier labels.

    SPREAD_FACTOR    Multiplied by std(model_values_at_h) to scale the
                     spread penalty in rain_agreement().  sf=0.1 is the
                     experimentally validated value (v109 experiment, seed=42,
                     2,000 blocks, rank score +0.042 vs sf=0.0).

    BRIDGE_THR       MME bias-corrected mean must reach this in every gap
                     hour for Phase 0 to bridge the gap as rainy.

    HEAVY_RAIN_THR   Consensus peak rain above this -> wx string "+RA".

    TEMPO_CUT        Minimum avg_agreement for plain TEMPO tier.
    PROB40_CUT       Minimum avg_agreement for PROB40 TEMPO tier.
    PROB30_CUT       Minimum avg_agreement for PROB30 TEMPO tier.
                     avg_agreement < PROB30_CUT -> suppressed.
                     [v112] Recalibrated from 0.40/0.30/0.20 -> 0.20/0.15/0.10
                     after switching to weighted votes in v110 compressed the
                     agreement distribution downward (grid-search optimised,
                     rank_score +0.076, FAR unchanged).  See header note.

    HEAVY_AGR_THR    Weighted fraction of models ≥ HEAVY_VOTE_THR required
                     before +RA is considered "multi-model confirmed".
                     Used ONLY for the sidebar warning flag - does NOT
                     auto-demote the wx string.

    HEAVY_VOTE_THR   Individual model value threshold for the +RA warning
                     agreement check.

    MAX_TEMPO_HOURS  Cap on TEMPO window length (prevents runaway windows).
    MAX_GROUPS       SOP Sec 12 hard cap on change groups per TAF.
    """
    CONSENSUS_THR   = 1.0    # mm - weighted mean gate
    VOTE_THR        = 1.0    # mm - per-model vote gate
    SPREAD_FACTOR   = 0.1    # dimensionless - spread penalty scalar
    BRIDGE_THR      = 0.20   # mm - MME corrected mean for dry-gap bridge
    HEAVY_RAIN_THR  = 4.0    # mm - peak consensus raw above this -> "+RA"
    TEMPO_CUT       = 0.18   # agreement fraction - TEMPO tier floor        [OPTIMAL]
    PROB40_CUT      = 0.1   # agreement fraction - PROB40 TEMPO tier floor  [OPTIMAL]
    PROB30_CUT      = 0.02   # agreement fraction - PROB30 TEMPO tier floor  [OPTIMAL]
    HEAVY_AGR_THR   = 0.40   # weighted agreement - +RA warning threshold
    HEAVY_VOTE_THR  = 3.0    # mm - per-model threshold for +RA warning check
    MAX_TEMPO_HOURS = 4      # hours - TEMPO window cap
                             # [FIX v113] Reverted from 5 to 4 with coordinated guard change.
                             # cap=4 + strict < guard was the v107.3 bug (D=2 silently suppressed).
                             # cap=4 + <= guard is correct: D=2 -> window=4 -> 2<=2.0 ✓
                             # Aligns with WIII operational standard (85% of TEMPOs ≤4h).
    MAX_GROUPS      = 5      # SOP Sec 12 group cap



# ==============================================================================
# SOP-COMPLIANT CHANGE GROUP BUILDER
# ==============================================================================

def _build_change_groups(
    consensus_truth: list[dict],
    valid_start: datetime,
    start_hour: int,
    meteo_data_local: dict,
    corrected_rain_data: dict,
    rain_timing_offset: int = -2,
    model_weights: dict | None = None,
    config=None,
) -> tuple[list[dict], list[str]]:
    # [v5.5.1] rain_timing_offset: integer hours to shift BECMG/TEMPO start
    # labels.  Applied ONLY to time_h_start in output dicts - NOT to the ts/te
    # index variables that drive rain-signal detection.  This ensures the
    # detection logic is unaffected; only the displayed change-group times shift.
    # Positive = push start later (model fires early, rain arrives late).
    # Negative = push start earlier (model fires late, rain arrives early).
    # Value is pre-capped to ±2h by the caller (_ensemble_timing_offset_h).
    """
    Build SOP/024/DM/X/2025-compliant change groups for the TAF.

    Triggers (only three, per operational data availability):
      • Rainfall    ≥ RainConfig.CONSENSUS_THR mm/h
      • Wind Speed  |Deltaspd| ≥ 10 kt from current prevailing
      • Wind Dir    circular Deltadir ≥ 60° AND speed before/after ≥ 10 kt

    Group type rules
    ----------------─
    BECMG       - condition persists ≥ 50% of 8-h lookahead.
                  BECMG window = 2 h (SOP "generally 2 h", min 1 h, max 4 h).
    TEMPO       - temporary fluctuation, rain model-agreement ≥ RainConfig.TEMPO_CUT.
                  Window sized so D < 50% of window (SOP literal requirement).
    PROB40 TEMPO - as TEMPO, agreement RainConfig.PROB40_CUT-TEMPO_CUT.
    PROB30 TEMPO - as TEMPO, agreement RainConfig.PROB30_CUT-PROB40_CUT.
    [suppressed] - agreement < RainConfig.PROB30_CUT OR no valid SOP window possible.
    Dry TEMPO    - temporary clearance, dry_agreement ≥ RainConfig.TEMPO_CUT.
                  (v109: symmetric quality gate with rainy TEMPO path.)

    Priority (used if > RainConfig.MAX_GROUPS groups):
      10 BECMG rain clearance
       9 BECMG rain onset / wind shift (permanent)
       8 BECMG rain onset (late-period)
       7 TEMPO rain (≥ TEMPO_CUT agreement)
       6 PROB40 TEMPO rain
       5 PROB30 TEMPO rain
       4 TEMPO wind (temporary)

    Parameters
    ----------
    corrected_rain_data : dict[str, list[float]]
        Bias-corrected hourly rainfall per model (keyed by model name).
        MUST be used for agreement fractions - using raw meteo_data_local
        would inflate agreement for models with a positive rainfall bias.
    """
    _warnings: list[str] = []
    # [Hindcast] Allow callers to inject a custom config for grid search.
    # Default: module-level RainConfig (no behavioural change for existing callers).
    _RC = config if config is not None else RainConfig
    MAX_GROUPS = _RC.MAX_GROUPS
    N = len(consensus_truth)
    if N == 0:
        return [], []  # [FIX BUG-1 v113] Must return tuple so callers can unpack (groups, warnings)

    # -- helpers --------------------------------------------------------------

    def circ_diff(a: float, b: float) -> float:
        d = abs(float(a) - float(b)) % 360.0
        return min(d, 360.0 - d)

    def rain_agreement(h: int) -> float:
        """
        Weighted agreement score: sum of weights of models whose
        bias-corrected + QM-corrected Rain ≥ _RC.VOTE_THR at hour h,
        normalised by the total weight of all loaded models, then adjusted
        by a spread penalty.

        FIX v105: uses corrected_rain_data (bias-corrected) instead of raw.
        FIX v108: VOTE_THR raised 0.8 -> 1.5 mm (experiment-derived).
        FIX v109: spread penalty applied after vote count.
        FIX v110: equal votes (count/total) replaced with weighted votes.

        MOTIVATION (March 2026 operational finding):
          With optimal_weights METEOBLUE=0.339, ACCESS-G3=0.206, ICON=0.186,
          ECMWF=0.166, GFS=0.103 - METEOBLUE alone predicting rain at 1.10mm
          MME produced a weighted consensus of 2.73mm but equal-vote agreement
          of only 0.20 x penalty = 0.157 (below PROB30 -> suppressed).
          METEOBLUE earned 3.3x the weight of GFS through historical CRPS
          performance; treating them as equal votes discards that information.

        Weighted vote formula:
          w_yes  = sum(model_weights[m] for m if corrected[m] >= VOTE_THR)
          w_tot  = sum(model_weights[m] for all loaded m)
          raw_agr = w_yes / w_tot   (falls back to count/total if no weights)

        With your weights at H18 (METEOBLUE alone votes):
          raw_agr = 0.339 / 1.000 = 0.339
          spread  ≈ 2.7 mm
          penalty = 1/(1 + 0.1 x 2.7) = 0.787
          final   = 0.339 x 0.787 = 0.267  -> PROB30 TEMPO  [OK]

        Spread penalty: unchanged from v109. Still meteorologically correct -
        a bimodal ensemble (one model high, four near zero) should be penalised
        harder than a coherent one even after switching to weighted votes.
        """
        w_yes      = 0.0
        w_tot      = 0.0
        count      = 0
        total      = 0
        model_vals = []

        for m in MODELS:
            rows = corrected_rain_data.get(m)
            if rows and h < len(rows):
                total += 1
                v = rows[h]
                model_vals.append(v)
                # Weight for this model - fall back to equal 1/N if not supplied
                w = float((model_weights or {}).get(m, 1.0 / len(MODELS)))
                w_tot += w
                if v >= _RC.VOTE_THR:
                    count += 1
                    w_yes += w

        if total == 0:
            return 0.0

        # Weighted agreement (normalised so missing models don't distort)
        if w_tot > 0 and model_weights:
            raw_agr = w_yes / w_tot
        else:
            # Fallback: equal votes (preserves old behaviour if weights absent)
            raw_agr = count / total

        # Spread penalty - only meaningful when ≥ 2 models loaded and
        # there is a non-zero agreement signal to penalise.
        if total >= 2 and raw_agr > 0:
            spread  = float(np.std(model_vals))
            penalty = 1.0 / (1.0 + _RC.SPREAD_FACTOR * spread)
            return raw_agr * penalty
        return raw_agr

    def avg_agreement(h0: int, h1: int) -> float:
        if h1 < h0:
            return 0.0
        vals = [rain_agreement(h) for h in range(h0, h1 + 1)]
        return sum(vals) / len(vals)

    def make_time_str(hs: int, he: int) -> str:
        """Convert raw h-indices (relative to valid_start) to a DDHH/DDHH string.
        No timing offset applied - use rain_time_str() for onset/TEMPO groups."""
        ts_dt = valid_start + timedelta(hours=hs)
        te_dt = valid_start + timedelta(hours=he)
        ts_utc = (start_hour + hs) % 24
        te_utc = (start_hour + he) % 24
        return f"{ts_dt.day:02d}{ts_utc:02d}/{te_dt.day:02d}{te_utc:02d}"

    def rain_time_str(hs: int, he: int) -> tuple | None:
        """
        [FIX v112] Apply rain_timing_offset then build the time string.

        Returns (time_str, time_h_start, time_h_end) as a consistent triple so
        the rendered TAF string and the internal dict fields always agree.

        Previously rain_timing_offset was applied ONLY to time_h_start (used
        for the suppression warning) but NOT to make_time_str (which produces
        the actual TAF text).  The offset therefore had zero effect on any
        rendered change group - a silent no-op.

        Use for rain-ONSET BECMG, TEMPO, PROB40 TEMPO, PROB30 TEMPO.
        Do NOT use for clearance BECMG (clearing bias not characterised) or
        wind BECMG (offset is rain-specific).

        Clamps both ends to [0, N] so the window stays inside TAF validity.
        Guarantees he_adj > hs_adj (non-zero window) even at TAF boundaries.

        [FIX v113 - TAF-boundary guard]
        Returns None when the offset pushes he_adj to the TAF end AND the
        window is only 1h wide (te == N after adjustment).  In that case the
        onset BECMG would say "RA permanently established at TAF expiry" -
        zero prevailing duration within the validity period, operationally
        useless.  Caller must suppress and emit a warning instead.

        TEMPO/PROB groups are exempt from this guard because they do not
        claim permanence - a TEMPO whose window reaches TAF end is valid.
        """
        hs_adj = max(0, min(N - 1, hs + rain_timing_offset))
        he_adj = max(0, min(N,     he + rain_timing_offset))
        if he_adj <= hs_adj:          # boundary squeeze -> force 1h minimum
            he_adj = min(hs_adj + 1, N)
        # TAF-boundary guard for onset BECMG: if the shift pushes the
        # establishment point (te) to the very end of the TAF the group is
        # meteorologically void.  Return None so the caller can suppress.
        if he_adj >= N and (he_adj - hs_adj) <= 1:
            return None
        return (
            make_time_str(hs_adj, he_adj),
            (start_hour + hs_adj) % 24,
            (start_hour + he_adj) % 24,
        )

    groups: list[dict] = []

    # -- base state ------------------------------------------------------------

    base_spd      = consensus_truth[0]["spd"]
    base_dir_num  = consensus_truth[0]["dir_num"]

    # -- helpers --------------------------------------------------------------─

    def becmg_window(ts: int, agr: float) -> int:
        """
        Return te for a BECMG group using a confidence-adaptive window [v5.6.0].

        High agreement (≥ TEMPO_CUT)  -> 1h window: onset is sharp and well-defined.
        Medium agreement               -> 2h window: SOP standard.
        Low agreement (< PROB40_CUT)   -> 3h window: uncertain onset.

        Guards against ts == te near TAF end: if the computed te equals ts the
        group would have a zero-length window (invalid ICAO Annex 3).  Returns
        None to signal the caller should suppress or handle differently.
        """
        if agr >= _RC.TEMPO_CUT:
            width = 1
        elif agr >= _RC.PROB40_CUT:
            width = 2
        else:
            width = 3
        te = min(ts + width, N - 1)
        return te if te > ts else None  # None = near-end suppression signal

    # -- PHASE 0 : MME-bridge rain signal ------------------------------------─
    #
    # The bias-corrected consensus uses a hard _RC.CONSENSUS_THR mm/h
    # threshold.  A brief dip below that level - even when the corrected MME
    # mean is clearly non-zero (e.g. 0.46) - caused Phase 1b to split one
    # continuous rain event into two separate blocks, producing an overlapping
    # TEMPO + BECMG pair.
    #
    # Fix: bridge gaps of ≤ 2 hours where every gap-hour's corrected MME mean
    # ≥ _RC.BRIDGE_THR and rain is confirmed on both sides.  The merged
    # block is then classified as permanent or temporary in one pass - no split,
    # no overlap.
    #
    # v109 fix: bridge threshold now checks mme_corrected_rain (bias-corrected
    # simple mean stored in consensus_truth) instead of mme_mean_rain (raw
    # uncorrected mean).  Using raw values in a tropical wet-bias environment
    # caused the bridge to fire too easily on noise.

    MME_BRIDGE_MM  = _RC.BRIDGE_THR
    MAX_BRIDGE_GAP = 2

    def _build_rain_signal() -> list[bool]:
        base    = [r["rain"] >= _RC.CONSENSUS_THR for r in consensus_truth]
        bridged = base[:]
        i = 0
        while i < N:
            if not base[i]:
                gap_start = i
                j = i
                while j < N and not base[j]:
                    j += 1
                gap_end = j
                gap_len = gap_end - gap_start
                if (gap_len <= MAX_BRIDGE_GAP
                        and gap_start > 0 and base[gap_start - 1]
                        and gap_end < N  and base[gap_end]
                        and all(
                            # v109: use bias-corrected MME mean, not raw
                            consensus_truth[h]["mme_corrected_rain"] >= MME_BRIDGE_MM
                            for h in range(gap_start, gap_end)
                        )):
                    for h in range(gap_start, gap_end):
                        bridged[h] = True
                i = gap_end if gap_end > i else i + 1
            else:
                i += 1
        return bridged

    rainy_sig = _build_rain_signal()

    # -- PHASE 1 : rainfall change groups ------------------------------------─
    N = len(consensus_truth)
    base_rain_sig = rainy_sig[0]
    prev_rain = base_rain_sig

    # 1a. Opening rain -> BECMG clearance (if base rainy)
    # [v5.6.0 TG-L4] Added dry-agreement gate: clearance BECMG is suppressed if
    # fewer than TEMPO_CUT fraction of models agree rain has actually cleared.
    # [v5.6.0 TG-C2] BECMG window is now confidence-adaptive via becmg_window().
    # [v5.6.0 TG-L1] Guard against zero-length BECMG window near TAF end.
    opening_rain_end = 0
    if base_rain_sig:
        rain_end = 0
        while rain_end < N and rainy_sig[rain_end]:
            rain_end += 1
        opening_rain_end = rain_end        # first hour that is no longer rainy
        if rain_end < N:                   # rain does clear before TAF end
            # Check how strongly models agree on the clearance
            clearance_dry_agr = sum(
                1.0 - rain_agreement(h)
                for h in range(rain_end, min(rain_end + 4, N))
            ) / min(4, N - rain_end)

            if clearance_dry_agr >= _RC.PROB40_CUT:
                ts = rain_end
                te = becmg_window(ts, clearance_dry_agr)
                if te is not None:          # TG-L1: suppress zero-length window
                    groups.append({
                        "type": "BECMG",
                        "time_str":     make_time_str(ts, te),
                        "time_h_start": (start_hour + ts) % 24,
                        "time_h_end":   (start_hour + te) % 24,
                        "wx": "NSW", "vis": "9999", "cloud": "SCT018",
                        "dir": "", "spd": "",
                        "_prio": 10, "_rain": 0.0,
                    })
                    prev_rain = False
                    i = te + 1
                else:
                    # Near TAF end - clearance cannot form valid window; skip
                    i = N
            else:
                # Weak clearance signal - suppress; forecaster to decide
                i = N
        else:
            # Rain lasts entire TAF, no change groups needed after base
            i = N
    else:
        # Base dry
        i = 0
        prev_rain = False

    def tempo_window_end(h0: int, D: int) -> int:
        """
        Return the end h-index of a TEMPO window.

        Formula 2*D+1 is computed first, then capped at MAX_TEMPO_HOURS = 4.
        The caller guard uses D <= window/2 (non-strict) which correctly
        admits D=2 in a 4h window at the 50% boundary.

        [FIX v113] cap=4 + D <= window/2 (non-strict guard at call sites):

          D=1 -> window=min(3,4)=3,  1 <= 1.5  ✓  TEMPO 3h
          D=2 -> window=min(5,4)=4,  2 <= 2.0  ✓  TEMPO 4h  ← key fix
          D=3 -> window=min(7,4)=4,  3 <= 2.0  ✗  falls to PROB/BECMG path
          D≥4 -> window=min(9,4)=4,  ≥4 <= 2.0 ✗  same

        History:
          v107.3: cap=4, strict < guard -> D=2 silently suppressed (bug)
          v107.3+: cap=5, strict < guard -> D=2 valid but 5h window (too wide)
          v113: cap=4, non-strict <= guard -> D=2 valid, 4h window ✓
              Aligns with WIII empirical standard (85% of TEMPOs are 3-4h).
        """
        MAX_TEMPO_HOURS = _RC.MAX_TEMPO_HOURS
        window_len = max(2 * D + 1, 2)
        window_len = min(window_len, MAX_TEMPO_HOURS)
        return min(h0 + window_len, N - 1)

    # 1b. Scan for subsequent rain/dry periods - use bridged rainy_sig
    while i < N:
        target_rain = not prev_rain
        # Find start of block
        start = i
        while start < N and rainy_sig[start] != target_rain:
            start += 1
        if start >= N:
            break
        # Find end of block (inclusive)
        end = start
        while end < N and rainy_sig[end] == target_rain:
            end += 1
        end -= 1
        D = end - start + 1

        # [v5.6.0 TG-C3] Permanence now uses 8-hour lookahead fraction, consistent
        # with Phase 2 wind logic.  Old D/remaining test was mathematically arbitrary
        # and produced BECMG for any rain starting in the final half of the TAF.
        look_end_rain = min(start + 8, N)
        if target_rain:
            n_matching = sum(1 for fh in range(start, look_end_rain) if rainy_sig[fh])
        else:
            n_matching = sum(1 for fh in range(start, look_end_rain) if not rainy_sig[fh])
        lookahead_len = look_end_rain - start
        # [FIX v111] Strict majority (> 0.5, not >= 0.5).
        # A tie - e.g. 3 rainy / 3 dry in a 6-hour lookahead - is NOT
        # permanent.  Rain H18-H20 followed by dry H21-H23 produced
        # fraction=0.500 which triggered BECMG.  Strict > 0.5 routes
        # ties correctly to the TEMPO path instead.
        #
        # [FIX v113 - MIN_LOOKAHEAD guard]
        # A block starting at H21 or later has fewer than 3 lookahead samples,
        # making the fraction statistically meaningless:
        #   H23 alone -> 1/1 = 1.000 -> always PERMANENT (degenerate)
        #   H22-H23   -> 2/2 = 1.000 -> always PERMANENT (degenerate)
        #
        # Real-world example: 23Z issuance, H23 (00Z) rain=0.80mm.
        # Lookahead = 1 sample -> fraction=1.0 -> BECMG 1423/1500.
        # Semantics: "RA permanently from 00Z" - but 00Z is TAF expiry.
        # Zero prevailing-RA duration inside the validity period.
        #
        # Fix: require at least MIN_LOOKAHEAD samples before trusting the
        # fraction.  Below that threshold the block is forced to TEMPORARY
        # so it enters the TEMPO/PROB path, which is either suppressed
        # (if agreement = 0) or issued as a PROB30/40 TEMPO - both
        # operationally safer than a meaningless tail-end BECMG.
        MIN_LOOKAHEAD = 3
        if lookahead_len < MIN_LOOKAHEAD:
            permanent = False   # too few samples - tail-end block, treat as temporary
        else:
            permanent = n_matching / lookahead_len > 0.5

        if target_rain:  # This is a rain block (dry -> rainy)
            total_rain = sum(consensus_truth[h]["rain_raw"] for h in range(start, end+1))
            peak_rain = max(consensus_truth[h]["rain_raw"] for h in range(start, end+1))
            wx_str = "+RA" if peak_rain >= _RC.HEAVY_RAIN_THR else "RA"
            agr = avg_agreement(start, end)

            # [FIX] If the block is long enough to be permanent, but model agreement 
            # is too low to issue a confident BECMG (< PROB40_CUT), DO NOT completely suppress it. 
            # Demote it to a temporary event so it can be safely caught by the PROB30 TEMPO logic.
            if permanent and agr < _RC.PROB40_CUT:
                permanent = False

            if permanent:
                # [v5.6.0 TG-L2] Agreement gate on permanent BECMG.
                # Previously issued unconditionally on duration alone.
                #
                # [FIX v112 - BECMG ONSET WINDOW DIRECTION]
                # ICAO Annex 3 semantics: the BECMG period is the transition
                # window, and conditions are PERMANENTLY ESTABLISHED at te
                # (the END of the window).
                #
                # Previous code used ts=start (first rainy hour) -> te=start+width.
                # This says "transition 13Z-14Z, rain permanent from 14Z."
                # But rain is ALREADY present at 13Z - the model placed it at H1.
                # So the permanent condition is established AT 13Z, not 14Z.
                #
                # Correct direction for rain ONSET:
                #   te = start            ← rain permanently established here
                #   ts = max(0, te-width) ← transition began width hours before
                #
                # width is confidence-adaptive (same logic as before):
                #   agr ≥ TEMPO_CUT:  width=1 (sharp, well-defined onset)
                #   agr ≥ PROB40_CUT: width=2 (standard 2h uncertainty)
                #   agr <  PROB40_CUT: width=3 (uncertain onset)
                #
                # Example (your case, start=H1=13Z, agr=0.339 ≥ TEMPO_CUT):
                #   te = 1 -> 13Z  (rain established at 13Z)
                #   ts = max(0, 1-1) = 0 -> 12Z
                #   -> BECMG 1212/1213  ✓
                #
                # Note: clearance BECMG uses the FORWARD direction (ts=first dry
                # hour, te=ts+width) and is NOT changed here - for clearance, the
                # dry condition is established at te (after width hours of drying).
                #
                # Guard: if start=0, rain begins at the very first TAF hour.
                # ts would be max(0,-1)=0=te - zero-length window.
                # In that case the base state handles it; no onset BECMG needed.
                if agr >= _RC.PROB40_CUT:
                    _width = 1 if agr >= _RC.TEMPO_CUT else (
                             2 if agr >= _RC.PROB40_CUT else 3)
                    te = start                    # rain permanently established here
                    ts = max(0, start - _width)   # transition starts _width hours before
                    if te > ts:   # valid window (guards start=0 edge case)
                        prio = 9 if agr >= _RC.TEMPO_CUT else 8
                        _rts = rain_time_str(ts, te)
                        if _rts is None:
                            # [FIX v113] Offset pushed onset to TAF boundary -
                            # permanent condition established at expiry = zero
                            # prevailing duration.  Suppress and warn.
                            utc_h = (start_hour + start) % 24
                            _warnings.append(
                                f"[WARN] RAIN ONSET SUPPRESSED - H{start} ({utc_h:02d}Z) rain "
                                f"detected but onset BECMG window falls at TAF expiry after "
                                f"timing offset ({rain_timing_offset:+d}h). "
                                f"Periksa secara manual."
                            )
                        else:
                            _tstr, _ths, _the = _rts
                            groups.append({
                                "type": "BECMG",
                                "time_str":     _tstr,
                                "time_h_start": _ths,
                                "time_h_end":   _the,
                                "wx": wx_str, "vis": "4000", "cloud": "BKN019",
                                "dir": "", "spd": "",
                                "_prio": prio, "_rain": total_rain,
                            })
                            prev_rain = True
                    else:
                        # Rain at H0 - base state carries it; no onset group needed
                        pass
                # else: agreement too low even for BECMG - suppress
            else:
                # Temporary rain - two window strategies:
                #
                # HIGH CONFIDENCE (agr ≥ TEMPO_CUT):
                #   Use strict SOP 2*D+1 formula.  The system is confident
                #   enough about duration that the uncertainty window is
                #   meaningful.  TG-C4 BECMG fallback applies for D≥3.
                #
                # PROB TIERS (agr < TEMPO_CUT):
                #   [FIX v111] Use compact window = D+1h (capped at 4h).
                #   Rationale: the PROB label already signals uncertainty.
                #   Stacking a wide 2*D+1 window on top of PROB40/PROB30
                #   is doubly conservative and misleading - a 2-hour shower
                #   should NOT produce a 5-hour PROB40 TEMPO window.
                #   The compact window covers the actual event span plus one
                #   buffer hour.  SOP window validity check is skipped for
                #   PROB tiers because the probability label communicates
                #   the uncertainty that the wide window was approximating.
                #
                # [v111 example] D=2 block at H08-H09, agr=0.316 (PROB40):
                #   Old: tempo_window_end -> 5h -> PROB40 TEMPO 1209/1214 (too wide)
                #   New: compact -> D+1=3h -> PROB40 TEMPO 1209/1212  ✓
                #
                # [v111 example] D=3 block at H18-H20, agr=0.267 (PROB30):
                #   Old: TG-C4 -> cap_end=22 -> PROB30 TEMPO 1219/1223
                #   New: compact -> D+1=4h -> PROB30 TEMPO 1219/1223  (same result,
                #        but now unified path - no TG-C4 BECMG risk for PROB tiers)

                if agr >= _RC.TEMPO_CUT:
                    # -- TEMPO (high confidence): strict SOP window ------------
                    t_end = tempo_window_end(start, D)
                    actual_window = t_end - start
                    if actual_window >= 1 and D <= actual_window / 2:  # [v113] <= (non-strict)
                        _rts = rain_time_str(start, t_end)  # [FIX BUG-2] guard None before unpack
                        if _rts is None:
                            utc_h = (start_hour + start) % 24
                            _warnings.append(
                                f"[WARN] TEMPO SUPPRESSED - H{start} ({utc_h:02d}Z) window at TAF "
                                f"boundary after timing offset ({rain_timing_offset:+d}h)."
                            )
                        else:
                            _tstr, _ths, _the = _rts
                            gtype, prio = "TEMPO", 7

                            # [Issue 2 - single-model dominance demotion]
                            # If plain TEMPO but agreement is marginal, check
                            # whether one model's weighted contribution exceeds
                            # 70% of total voting weight at the peak hour.
                            # If so, demote to PROB40 TEMPO - plain TEMPO
                            # implies ~70% verification rate which a single
                            # model at threshold cannot support.
                            if agr < (_RC.TEMPO_CUT + 0.05):
                                _peak_h = max(
                                    range(start, min(t_end + 1, len(
                                        corrected_rain_data.get(MODELS[0], [])
                                    ))),
                                    key=lambda h: sum(
                                        1 for m in MODELS
                                        if (corrected_rain_data.get(m)
                                            and h < len(corrected_rain_data[m])
                                            and corrected_rain_data[m][h]
                                                >= _RC.VOTE_THR)
                                    ),
                                    default=start,
                                )
                                _model_w = {
                                    m: float((model_weights or {}).get(
                                        m, 1.0 / len(MODELS)))
                                    for m in MODELS
                                    if (corrected_rain_data.get(m)
                                        and _peak_h < len(corrected_rain_data[m])
                                        and corrected_rain_data[m][_peak_h]
                                            >= _RC.VOTE_THR)
                                }
                                _total_w = sum(_model_w.values())
                                if (_total_w > 0
                                        and max(_model_w.values()) / _total_w > 0.70):
                                    gtype = "PROB40 TEMPO"
                                    prio  = 6

                            groups.append({
                                "type": gtype,
                                "time_str":     _tstr,
                                "time_h_start": _ths,
                                "time_h_end":   _the,
                                "wx": wx_str, "vis": "4000", "cloud": "BKN019",
                                "dir": "", "spd": "",
                                "_prio": prio, "_rain": total_rain,
                            })
                        # TEMPO does not change prevailing state
                    else:
                        # [TG-C4 v112] D≥3: cannot form a valid TEMPO window
                        # (SOP requires D < window/2).
                        #
                        # PREVIOUS behaviour: fall back to BECMG.
                        # BUG: a permanent=False block must NEVER produce BECMG.
                        # The permanence check already decided this event is
                        # temporary.  Issuing BECMG contradicts that decision
                        # and incorrectly sets prev_rain=True.
                        #
                        # FIXED behaviour (v112): demote to PROB40 compact window.
                        # Rationale: the TEMPO_CUT agreement is genuine but the
                        # D≥3 duration prevents a valid TEMPO window.  PROB40 is
                        # the strongest output we can honestly issue within SOP.
                        # The compact D+1h window caps it at 4h max.
                        #
                        # Interaction note: this bug was latent in v111 because
                        # TEMPO_CUT=0.40 meant typical single-model events rarely
                        # reached TEMPO tier.  After recalibration to 0.20 (v112),
                        # many more temporary events enter TEMPO path and hit TG-C4.
                        t_end = min(start + min(D + 1, 4), N - 1)
                        if t_end > start:
                            _rts = rain_time_str(start, t_end)  # [FIX BUG-2]
                            if _rts is not None:
                                _tstr, _ths, _the = _rts
                                groups.append({
                                    "type": "PROB40 TEMPO",
                                    "time_str":     _tstr,
                                    "time_h_start": _ths,
                                    "time_h_end":   _the,
                                    "wx": wx_str, "vis": "4000", "cloud": "BKN019",
                                    "dir": "", "spd": "",
                                    "_prio": 6, "_rain": total_rain,
                                })
                        # PROB40 TEMPO does not change prevailing state

                elif agr >= _RC.PROB40_CUT:
                    # -- PROB40 TEMPO: compact window ------------------------─
                    t_end = min(start + min(D + 1, 4), N - 1)
                    if t_end > start:
                        _rts = rain_time_str(start, t_end)  # [FIX BUG-2]
                        if _rts is not None:
                            _tstr, _ths, _the = _rts
                            groups.append({
                                "type": "PROB40 TEMPO",
                                "time_str":     _tstr,
                                "time_h_start": _ths,
                                "time_h_end":   _the,
                                "wx": wx_str, "vis": "4000", "cloud": "BKN019",
                                "dir": "", "spd": "",
                                "_prio": 6, "_rain": total_rain,
                            })
                    # PROB TEMPO does not change prevailing state

                elif agr >= _RC.PROB30_CUT:
                    # -- PROB30 TEMPO: compact window ------------------------─
                    t_end = min(start + min(D + 1, 4), N - 1)
                    if t_end > start:
                        _rts = rain_time_str(start, t_end)  # [FIX BUG-2]
                        if _rts is not None:
                            _tstr, _ths, _the = _rts
                            groups.append({
                                "type": "PROB30 TEMPO",
                                "time_str":     _tstr,
                                "time_h_start": _ths,
                                "time_h_end":   _the,
                                "wx": wx_str, "vis": "4000", "cloud": "BKN019",
                                "dir": "", "spd": "",
                                "_prio": 5, "_rain": total_rain,
                            })
                    # PROB TEMPO does not change prevailing state

                else:
                    # Suppress - agreement below PROB30_CUT
                    i = end + 1
                    continue

        else:  # target_rain is False -> this is a dry block (rainy -> dry)
            # Note: genuine sandwiched dry gaps (MME still non-zero) will have
            # been bridged to "rainy" by Phase 0, so they never reach here.
            # Any dry block that does reach here is a real clearance event.
            #
            # [FIX v112] Compute actual avg_dry_agr for BOTH permanent and
            # temporary branches.  The permanent branch previously used a
            # hardcoded _RC.TEMPO_CUT as the confidence passed to
            # becmg_window(), which set a fixed 2h window regardless of how
            # strongly models agreed on the clearance.  A high-confidence
            # clearance (all models dry) should produce the same tight 1h
            # window that a high-confidence rain onset does.
            avg_dry_agr = sum(
                1.0 - rain_agreement(h) for h in range(start, end + 1)
            ) / (end - start + 1)

            if permanent:
                ts = start
                te = becmg_window(ts, avg_dry_agr)   # [FIX] was _RC.TEMPO_CUT
                if te is not None:   # TG-L1: guard zero-length window
                    groups.append({
                        "type": "BECMG",
                        "time_str": make_time_str(ts, te),
                        "time_h_start": (start_hour + ts) % 24,
                        "time_h_end": (start_hour + te) % 24,
                        "wx": "NSW", "vis": "9999", "cloud": "SCT018",
                        "dir": "", "spd": "",
                        "_prio": 8, "_rain": 0.0,
                    })
                    prev_rain = False
            else:
                # Temporary dry period
                # [v5.6.0 TG-C1] Added PROB40/PROB30 tiers for dry blocks,
                # symmetric with the rainy TEMPO path.  Previously only plain
                # TEMPO or suppression - a 35%-clearance-agreement event was
                # fully suppressed with no output, leaving the forecaster unaware.
                # avg_dry_agr already computed above (shared with permanent branch).

                if avg_dry_agr >= _RC.TEMPO_CUT:
                    gtype_dry = "TEMPO"
                elif avg_dry_agr >= _RC.PROB40_CUT:
                    gtype_dry = "PROB40 TEMPO"
                elif avg_dry_agr >= _RC.PROB30_CUT:
                    gtype_dry = "PROB30 TEMPO"
                else:
                    # Not enough models agree on clearance - suppress
                    i = end + 1
                    continue

                t_end = tempo_window_end(start, D)
                actual_window = t_end - start
                if actual_window >= 1 and D <= actual_window / 2:  # [v113] <= (non-strict)
                    groups.append({
                        "type": gtype_dry,   # [FIX BUG-5] was missing - causes KeyError downstream
                        "time_str": make_time_str(start, t_end),
                        "time_h_start": (start_hour + start) % 24,
                        "time_h_end":   (start_hour + t_end) % 24,
                        "wx": "NSW", "vis": "9999", "cloud": "SCT018",
                        "dir": "", "spd": "",
                        "_prio": 5, "_rain": 0.0,
                    })
                    # TEMPO does not change prevailing state
                else:
                    # Cannot make valid TEMPO window, suppress
                    i = end + 1
                    continue

        # Move i past this block
        i = end + 1
    # -- PHASE 2 : wind change groups ------------------------------------------
    # prev_* tracks the current prevailing wind so we don't re-trigger on the
    # same sustained deviation once a BECMG has been issued for it.

    prev_spd     = base_spd
    prev_dir_num = base_dir_num
    i            = 1

    while i < N:
        curr = consensus_truth[i]
        spd_delta = abs(curr["spd"] - prev_spd)
        dir_delta = circ_diff(curr["dir_num"], prev_dir_num)

        # SOP Sec 12.i wind trigger criteria
        spd_trigger = spd_delta >= 10
        dir_trigger = dir_delta >= 60 and (curr["spd"] >= 10 or prev_spd >= 10)

        if not (spd_trigger or dir_trigger):
            i += 1
            continue

        # End of deviation period
        end_h = i
        while end_h < N:
            fsd = abs(consensus_truth[end_h]["spd"] - prev_spd)
            fdd = circ_diff(consensus_truth[end_h]["dir_num"], prev_dir_num)
            if not (fsd >= 10 or (fdd >= 60 and consensus_truth[end_h]["spd"] >= 10)):
                break
            end_h += 1
        D_wind = max(1, end_h - i)

        # Persistence check: fraction of 8-h lookahead that is deviated
        look_end = min(i + 8, N)
        persist  = sum(
            1 for fh in range(i, look_end)
            if abs(consensus_truth[fh]["spd"] - prev_spd) >= 10
            or (circ_diff(consensus_truth[fh]["dir_num"], prev_dir_num) >= 60
                and consensus_truth[fh]["spd"] >= 10)
        )
        fraction = persist / (look_end - i) if look_end > i else 0.0

        new_dir = curr["dir"]
        new_spd = str(curr["spd"]).zfill(2)

        if fraction >= 0.5:
            # Permanent wind change -> BECMG
            # [v5.6.0 TG-C2] Adaptive window: high persistence -> 1h, medium -> 2h.
            # [v5.6.0 TG-L1] Guard against zero-length window near TAF end.
            # [FIX v112] Window direction corrected (same logic as rain onset BECMG):
            #   te = i  (new wind established at hour i - it's already changed here)
            #   ts = max(0, i - width)  (transition started width hours before)
            _width_w = 1 if fraction >= 0.7 else 2
            te = i
            ts = max(0, i - _width_w)
            if te <= ts:   # i=0 edge case - no room for transition
                i = end_h + 1
                continue
            groups.append({
                "type": "BECMG",
                "time_str":    make_time_str(ts, te),
                "time_h_start": (start_hour + ts) % 24,
                "time_h_end":   (start_hour + te) % 24,
                "wx": "", "vis": "", "cloud": "",
                "dir": new_dir, "spd": new_spd,
                "_prio": 9, "_rain": 0.0,
            })
            prev_spd     = curr["spd"]
            prev_dir_num = curr["dir_num"]
            i            = te + 1

        else:
            # Temporary wind deviation -> TEMPO
            t_end         = tempo_window_end(i, D_wind)
            actual_window = t_end - i

            if actual_window < 1 or D_wind > actual_window / 2:
                # TEMPO window invalid for this duration (D > 2 after cap=4).  [v113]
                # Previously this branch silently dropped the group with `continue`.
                # Fix (Gap E): if the deviation has ≥ 30% persistence in the 8-h
                # lookahead, escalate to BECMG rather than discarding it entirely.
                # [v5.6.0 TG-L1] Guard against zero-length window near TAF end.
                if fraction >= 0.3:
                    # [FIX v112] Same backward-looking direction as onset BECMG.
                    # te = i (change established at hour i), ts = i - width
                    _width_wf = 1 if fraction >= 0.7 else 2
                    te = i
                    ts = max(0, i - _width_wf)
                    if te <= ts:
                        i = end_h + 1
                        continue
                    groups.append({
                        "type": "BECMG",
                        "time_str":    make_time_str(ts, te),
                        "time_h_start": (start_hour + ts) % 24,
                        "time_h_end":   (start_hour + te) % 24,
                        "wx": "", "vis": "", "cloud": "",
                        "dir": new_dir, "spd": new_spd,
                        "_prio": 8, "_rain": 0.0,
                    })
                    prev_spd     = curr["spd"]
                    prev_dir_num = curr["dir_num"]
                    i            = te + 1
                else:
                    i = end_h + 1
                continue

            groups.append({
                "type": "TEMPO",
                "time_str":    make_time_str(i, t_end),
                "time_h_start": (start_hour + i) % 24,
                "time_h_end":   (start_hour + t_end) % 24,
                "wx": "", "vis": "", "cloud": "",
                "dir": new_dir, "spd": new_spd,
                "_prio": 4, "_rain": 0.0,
            })
            i = end_h + 1

    # -- PHASE 3 : merge overlapping TEMPO groups ------------------------------
    # A rain-TEMPO and a wind-TEMPO covering the same window are merged so
    # the single group carries both wind AND weather information.

    def _hs(g: dict) -> int:
        return (g["time_h_start"] - start_hour) % 24

    def _he(g: dict) -> int:
        return (g["time_h_end"] - start_hour) % 24

    groups.sort(key=_hs)
    merged: list[dict] = []
    for g in groups:
        if (merged
                and "TEMPO" in merged[-1]["type"]
                and "TEMPO" in g["type"]
                and _he(merged[-1]) >= _hs(g)):
            last    = merged[-1]
            new_end = max(_he(last), _he(g))
            if g.get("wx"):    last["wx"]    = g["wx"]
            if g.get("vis"):   last["vis"]   = g["vis"]
            if g.get("cloud"): last["cloud"] = g["cloud"]
            if g.get("dir"):   last["dir"]   = g["dir"]
            if g.get("spd"):   last["spd"]   = g["spd"]
            last["_prio"]       = max(last["_prio"],  g["_prio"])
            last["_rain"]       = max(last["_rain"],  g["_rain"])
            last["time_str"]    = make_time_str(_hs(last), new_end)
            last["time_h_end"]  = (start_hour + new_end) % 24
            # [v107 TG-6 fix] Elevate the merged group's type to the highest
            # CONFIDENCE present.  ICAO TAF semantics:
            #   TEMPO         = event expected to occur (highest confidence)
            #   PROB40 TEMPO  = 30-40% probability (medium confidence)
            #   PROB30 TEMPO  = <30% probability (lowest confidence)
            # When merging, keep the HIGHEST confidence tier.
            #
            # [v114 fix] Previously inverted: treated PROB40 as highest.
            # Corrected: plain TEMPO > PROB40 TEMPO > PROB30 TEMPO.
            def _elevate_type(t1: str, t2: str) -> str:
                # Plain TEMPO (no PROB prefix) = highest confidence
                if ("TEMPO" in t1 and "PROB" not in t1) or \
                   ("TEMPO" in t2 and "PROB" not in t2):
                    return "TEMPO"
                if "PROB40" in t1 or "PROB40" in t2:
                    return "PROB40 TEMPO"
                return "PROB30 TEMPO"
            last["type"] = _elevate_type(last["type"], g["type"])

            # [Gap F fix v107.4] Re-validate the merged window.
            # Two 5h TEMPOs that partially overlap can merge into a window
            # longer than 5h.  At that point the "D < window/2" guarantee
            # no longer holds for the combined event, and the over-wide
            # TEMPO would mislead the forecaster.
            # Rule: if merged window > MAX_TEMPO_HOURS (5h), escalate the
            # group to BECMG - a sustained composite event lasting > 5h is
            # a permanent change by any operational definition.
            merged_window = _he(last) - _hs(last)
            MAX_MERGED_TEMPO = _RC.MAX_TEMPO_HOURS
            if merged_window > MAX_MERGED_TEMPO:
                last["type"] = "BECMG"
                # [v5.6.0] Use adaptive BECMG window (1h if high-prio, else 2h).
                # Previously always stamped +2h which could still hit N-1.
                new_becmg_end = min(_hs(last) + (1 if last["_prio"] >= 9 else 2), N - 1)
                if new_becmg_end <= _hs(last):
                    new_becmg_end = min(_hs(last) + 1, N - 1)
                last["time_str"]   = make_time_str(_hs(last), new_becmg_end)
                last["time_h_end"] = (start_hour + new_becmg_end) % 24
                last["_prio"]      = 9   # BECMG rain onset priority
        else:
            merged.append(dict(g))
    groups = merged

    # -- PHASE 3b : resolve TEMPO ↔ BECMG overlaps ----------------------------
    #
    # Problem: a TEMPO whose inflated window (2*D+1) extends past a later BECMG
    # start creates an overlapping pair that is invalid per SOP Sec 12.h.3
    # ("harus dihindari periode waktu yang overlapping").
    #
    # Typical pattern that triggers this:
    #   H11-H14  temporary rain  -> PROB40 TEMPO 2317/2402  (window 9 h)
    #   H16-H23  permanent rain  -> BECMG       2322/2400
    #   Overlap: 22Z-02Z
    #
    # Resolution rule: truncate the TEMPO's end to the BECMG's start.
    # If the resulting TEMPO window shrinks below 1 h it is meaningless
    # - drop it (the BECMG already covers the rain onset for the user).
    #
    # Applied iteratively so a chain of 3+ groups is also handled.

    def _resolve_tempo_becmg_overlaps(grps: list[dict]) -> list[dict]:
        changed = True
        while changed:
            changed = False
            out: list[dict] = []
            for g in grps:
                if (out
                        and "TEMPO" in out[-1]["type"]
                        and out[-1].get("type", "") not in ("BECMG",)
                        and "BECMG" in g["type"]
                        and _he(out[-1]) > _hs(g)):   # overlap detected
                    prev = out[-1]
                    new_end_h = _hs(g)                # truncate to BECMG start
                    if new_end_h - _hs(prev) >= 1:   # keep if window ≥ 1 h
                        prev["time_str"]   = make_time_str(_hs(prev), new_end_h)
                        prev["time_h_end"] = (start_hour + new_end_h) % 24
                        out.append(g)
                    else:
                        # TEMPO window collapsed - drop it, keep only BECMG
                        out.pop()
                        out.append(g)
                    changed = True
                else:
                    out.append(g)
            grps = out
        return grps

    groups = _resolve_tempo_becmg_overlaps(groups)

    # -- PHASE 4 : enforce SOP 5-group cap ------------------------------------
    if len(groups) > MAX_GROUPS:
        groups.sort(key=lambda g: g["_prio"], reverse=True)
        groups = groups[:MAX_GROUPS]
        groups.sort(key=_hs)

    # Strip internal keys before returning to JS
    near_end_flags = [g.pop("_near_end", False) for g in groups]
    for g in groups:
        g.pop("_prio", None)
        g.pop("_rain", None)

    # Collect warning for near-TAF-end groups; returned to caller for display
    if any(near_end_flags):
        _warnings.append(
            "[WARN] Satu atau lebih perubahan cuaca terjadi mendekati akhir periode TAF. "
            "Jendela BECMG tidak dapat terbentuk - ditampilkan sebagai PROB40 TEMPO. "
            "Periksa secara manual."
        )

    return groups, _warnings
