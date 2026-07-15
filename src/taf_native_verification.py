"""TAF-native verification for the June 2026 original WAWP TAF archive.

This experiment evaluates human-issued TAFs against the source METAR archive
without modifying a workbook, the operational database, or live guidance.
It implements the parts of the Austro Control-style methodology that are
meaningful with the available half-hourly METAR observations:

* prevailing conditions are verified separately from temporary/probability
  event windows;
* BECMG is a transition range that persists once complete;
* TEMPO is a deterministic event-warning window;
* PROB30/PROB40 is kept probabilistic and evaluated with Brier score instead
  of being silently converted into a deterministic warning;
* no arbitrary +/- timing allowance is used in the primary event score.

The result deliberately reports both sample-based and event-episode scores.
The former retains correct dry observations for HSS; the latter avoids giving a
long temporary group credit for several separate observed rain episodes.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.legacy_taf_verification import (
    HEADER_RE,
    WeatherState,
    _day_candidates,
    _month_shift,
    _overlay_state,
    _parse_state,
    parse_metar,
    parse_taf,
    repair_validity_to_24h,
)
from src.taf_replay_june_holdout import (
    PERIOD,
    _weather_has_rain,
    _weather_has_thunderstorm,
    extract_june_workbook,
)


UTC = timezone.utc
GROUP_RE = re.compile(
    r"\b(?:"
    r"(?P<fm>FM(?P<fm_day>\d{2})(?P<fm_hour>\d{2})(?P<fm_minute>\d{2}))"
    r"|(?P<kind>BECMG|TEMPO|PROB(?P<probability>30|40)(?:\s+TEMPO)?)\s+"
    r"(?P<window>\d{4}/\d{4})"
    r")\b"
)
CLOUD_LAYER_RE = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})(?:CB|TCU)?\b")
VV_RE = re.compile(r"\bVV(\d{3})\b")


@dataclass(frozen=True)
class NativeGroup:
    kind: str
    start: datetime
    end: datetime
    state: WeatherState
    probability: float | None = None


@dataclass(frozen=True)
class NativeTAF:
    text: str
    issue_time: datetime
    valid_start: datetime
    valid_end: datetime
    base: WeatherState
    groups: tuple[NativeGroup, ...]


def _resolve_window(year: int, month: int, token: str, anchor: datetime) -> tuple[datetime, datetime]:
    start_day, start_hour = int(token[:2]), int(token[2:4])
    end_day, end_hour = int(token[5:7]), int(token[7:9])
    starts = _day_candidates(year, month, start_day, start_hour)
    start = min(starts, key=lambda value: abs((value - anchor).total_seconds()))
    ends = [value for value in _day_candidates(year, month, end_day, end_hour) if value > start]
    if not ends:
        raise ValueError(f"Invalid group window {token!r}")
    return start, min(ends)


def _resolve_fm(year: int, month: int, day: int, hour: int, minute: int, anchor: datetime) -> datetime:
    candidates: list[datetime] = []
    for delta in (-1, 0, 1):
        candidate_year, candidate_month = _month_shift(year, month, delta)
        try:
            candidates.append(datetime(candidate_year, candidate_month, day, hour, minute, tzinfo=UTC))
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f"Invalid FM group {day:02d}{hour:02d}{minute:02d}")
    return min(candidates, key=lambda value: abs((value - anchor).total_seconds()))


def parse_native_taf(text: str, period: str = PERIOD) -> NativeTAF:
    """Parse enough TAF grammar for auditable range/event verification."""
    base_taf, _, _ = repair_validity_to_24h(parse_taf(text, period))
    compact = base_taf.text
    year, month = (int(value) for value in period.split("-"))
    matches = list(GROUP_RE.finditer(compact))
    header = HEADER_RE.search(compact)
    if not header:
        raise ValueError(f"Malformed WAWP TAF: {text!r}")
    base_end = matches[0].start() if matches else len(compact)
    parsed_base = _parse_state(compact[header.end() : base_end])
    base = WeatherState(
        wind_direction=parsed_base.wind_direction,
        wind_is_variable=parsed_base.wind_is_variable,
        wind_speed=parsed_base.wind_speed,
        wind_gust=parsed_base.wind_gust,
        visibility_m=parsed_base.visibility_m,
        weather=parsed_base.weather or "0",
        cloud_amount=parsed_base.cloud_amount,
        cloud_base_ft=parsed_base.cloud_base_ft,
    )
    groups: list[NativeGroup] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(compact)
        remainder = compact[match.end() : next_start]
        if match.group("fm"):
            start = _resolve_fm(
                year,
                month,
                int(match.group("fm_day")),
                int(match.group("fm_hour")),
                int(match.group("fm_minute")),
                base_taf.valid_start,
            )
            kind = "FM"
            end = base_taf.valid_end
            probability = None
        else:
            start, end = _resolve_window(year, month, match.group("window"), base_taf.valid_start)
            raw_kind = match.group("kind")
            kind = "PROB_TEMPO" if raw_kind.startswith("PROB") and "TEMPO" in raw_kind else (
                "PROB" if raw_kind.startswith("PROB") else raw_kind
            )
            probability = (int(match.group("probability")) / 100.0) if match.group("probability") else None
        groups.append(NativeGroup(kind, start, end, _parse_state(remainder), probability))
    return NativeTAF(
        text=compact,
        issue_time=base_taf.issue_time,
        valid_start=base_taf.valid_start,
        valid_end=base_taf.valid_end,
        # Legacy ``parse_taf`` does not recognise a standalone PROB group as a
        # base-state boundary.  The native parser must, otherwise a PROB
        # weather token can incorrectly leak into prevailing conditions.
        base=base,
        groups=tuple(groups),
    )


def _prevailing_state(taf: NativeTAF, at: datetime) -> WeatherState:
    state = taf.base
    for group in taf.groups:
        if group.kind == "FM" and at >= group.start:
            state = _overlay_state(state, group.state)
        elif group.kind == "BECMG" and at >= group.end:
            state = _overlay_state(state, group.state)
    return state


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and other_start < end


def _hour_state_candidates(taf: NativeTAF, start: datetime) -> list[WeatherState]:
    """Return all TAF states that can apply in an hour, including transitions."""
    end = start + timedelta(hours=1)
    candidates = [_prevailing_state(taf, start), _prevailing_state(taf, end - timedelta(seconds=1))]
    for group in taf.groups:
        if group.kind in {"TEMPO", "PROB", "PROB_TEMPO"} and _overlaps(start, end, group.start, group.end):
            candidates.append(_overlay_state(_prevailing_state(taf, max(start, group.start)), group.state))
        elif group.kind == "BECMG" and _overlaps(start, end, group.start, group.end):
            candidates.append(_overlay_state(_prevailing_state(taf, max(start, group.start)), group.state))
        elif group.kind == "FM" and start <= group.start < end:
            candidates.append(_overlay_state(_prevailing_state(taf, start), group.state))
    unique: list[WeatherState] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _weather_has_event(weather: str | None, event: str) -> bool:
    return _weather_has_rain(weather or "") if event == "rain" else _weather_has_thunderstorm(weather or "")


def _merge_intervals(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _prevailing_event_intervals(taf: NativeTAF, event: str) -> list[tuple[datetime, datetime]]:
    boundaries = {taf.valid_start, taf.valid_end}
    for group in taf.groups:
        if group.kind == "FM":
            boundaries.add(group.start)
        elif group.kind == "BECMG":
            boundaries.add(group.end)
    ordered = sorted(boundary for boundary in boundaries if taf.valid_start <= boundary <= taf.valid_end)
    result: list[tuple[datetime, datetime]] = []
    for start, end in zip(ordered, ordered[1:]):
        if _weather_has_event(_prevailing_state(taf, start).weather, event):
            result.append((start, end))
    return result


def forecast_event_intervals(taf: NativeTAF, event: str, *, include_probability: bool = False) -> list[tuple[datetime, datetime]]:
    """Build deterministic alert intervals without a timing grace period."""
    intervals = _prevailing_event_intervals(taf, event)
    for group in taf.groups:
        if not _weather_has_event(group.state.weather, event):
            continue
        if group.kind == "TEMPO" or (include_probability and group.kind in {"PROB", "PROB_TEMPO"}):
            intervals.append((max(group.start, taf.valid_start), min(group.end, taf.valid_end)))
        elif group.kind == "BECMG":
            # During BECMG either the before or after state can occur; after the
            # end, the changed state is prevailing and is covered above.
            intervals.append((max(group.start, taf.valid_start), min(group.end, taf.valid_end)))
    return _merge_intervals(intervals)


def observed_event_episodes(
    observations: list[dict[str, Any]],
    event: str,
    *,
    sample_interval: timedelta = timedelta(minutes=30),
) -> list[tuple[datetime, datetime]]:
    active = [
        datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00"))
        for row in observations
        if _weather_has_event(str(row["metar_text"]), event)
    ]
    result: list[tuple[datetime, datetime]] = []
    for at in sorted(active):
        end = at + sample_interval
        if result and at <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((at, end))
    return result


def _interval_match_counts(
    forecast: list[tuple[datetime, datetime]], observed: list[tuple[datetime, datetime]]
) -> dict[str, int]:
    used_forecast: set[int] = set()
    hits = 0
    for observed_start, observed_end in observed:
        choices = [
            index for index, (forecast_start, forecast_end) in enumerate(forecast)
            if index not in used_forecast and _overlaps(forecast_start, forecast_end, observed_start, observed_end)
        ]
        if choices:
            selected = max(
                choices,
                key=lambda index: min(forecast[index][1], observed_end) - max(forecast[index][0], observed_start),
            )
            used_forecast.add(selected)
            hits += 1
    return {
        "forecast_episodes": len(forecast),
        "observed_episodes": len(observed),
        "hits": hits,
        "misses": len(observed) - hits,
        "false_alarms": len(forecast) - hits,
    }


def _event_metrics(counts: dict[str, int]) -> dict[str, int | float | None]:
    hits, misses, false_alarms = counts["hits"], counts["misses"], counts["false_alarms"]
    return {
        **counts,
        "POD": round(hits / (hits + misses), 4) if hits + misses else None,
        "FAR": round(false_alarms / (hits + false_alarms), 4) if hits + false_alarms else None,
        "CSI": round(hits / (hits + misses + false_alarms), 4) if hits + misses + false_alarms else None,
        "frequency_bias": round((hits + false_alarms) / (hits + misses), 4) if hits + misses else None,
    }


def _sample_event_metrics(rows: list[dict[str, bool]]) -> dict[str, int | float | None]:
    hits = misses = false_alarms = correct_negatives = 0
    for row in rows:
        if row["forecast"] and row["observed"]:
            hits += 1
        elif row["forecast"]:
            false_alarms += 1
        elif row["observed"]:
            misses += 1
        else:
            correct_negatives += 1
    total = hits + misses + false_alarms + correct_negatives
    expected = (
        ((hits + misses) * (hits + false_alarms))
        + ((correct_negatives + misses) * (correct_negatives + false_alarms))
    ) / total if total else 0.0
    denominator = total - expected
    return {
        "sample_size": total,
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
        "accuracy": round((hits + correct_negatives) / total, 4) if total else None,
        "POD": round(hits / (hits + misses), 4) if hits + misses else None,
        "FAR": round(false_alarms / (hits + false_alarms), 4) if hits + false_alarms else None,
        "CSI": round(hits / (hits + misses + false_alarms), 4) if hits + misses + false_alarms else None,
        "HSS": round((hits + correct_negatives - expected) / denominator, 4) if denominator else None,
    }


def _visibility_band(value: int | None) -> int | None:
    if value is None:
        return None
    thresholds = (150, 350, 600, 800, 1500, 3000, 5000)
    for index, threshold in enumerate(thresholds):
        if value < threshold:
            return index
    return len(thresholds)


def _ceiling_band(text: str) -> int:
    layers = [int(base) * 100 for amount, base in CLOUD_LAYER_RE.findall(text) if amount in {"BKN", "OVC"}]
    vertical = [int(base) * 100 for base in VV_RE.findall(text)]
    base = min(layers + vertical) if layers or vertical else 9999
    thresholds = (150, 350, 600, 800, 1500, 3000, 5000)
    for index, threshold in enumerate(thresholds):
        if base < threshold:
            return index
    return len(thresholds)


def _range_rows(taf: NativeTAF, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_hour: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        at = datetime.fromisoformat(observation["observed_at_utc"].replace("Z", "+00:00"))
        by_hour[at.replace(minute=0, second=0, microsecond=0)].append(observation)
    result: list[dict[str, Any]] = []
    current = taf.valid_start
    while current < taf.valid_end:
        hour_observations = by_hour.get(current, [])
        if hour_observations:
            states = _hour_state_candidates(taf, current)
            forecast_visibility = [_visibility_band(state.visibility_m) for state in states if _visibility_band(state.visibility_m) is not None]
            observed_states = [parse_metar(row["metar_text"]) for row in hour_observations]
            observed_visibility = [_visibility_band(state.visibility_m) for state in observed_states if _visibility_band(state.visibility_m) is not None]
            if forecast_visibility and observed_visibility:
                result.append({
                    "issuance_utc": taf.issue_time.isoformat().replace("+00:00", "Z"),
                    "valid_hour_utc": current.isoformat().replace("+00:00", "Z"),
                    "element": "visibility",
                    "forecast_min_band": min(forecast_visibility),
                    "forecast_max_band": max(forecast_visibility),
                    "observed_min_band": min(observed_visibility),
                    "observed_max_band": max(observed_visibility),
                })
            # WeatherState stores only its first cloud layer. Re-reading an
            # individual parsed state would lose a later BKN/OVC layer after
            # FEW/SCT, so this compact verifier does not claim a ceiling score.
        current += timedelta(hours=1)
    return result


def _summarize_ranges(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for element in {row["element"] for row in rows}:
        relevant = [row for row in rows if row["element"] == element]
        min_hits = sum(row["forecast_min_band"] == row["observed_min_band"] for row in relevant)
        max_hits = sum(row["forecast_max_band"] == row["observed_max_band"] for row in relevant)
        result[element] = {
            "eligible_hours": len(relevant),
            "minimum_category_accuracy": round(min_hits / len(relevant), 4) if relevant else None,
            "maximum_category_accuracy": round(max_hits / len(relevant), 4) if relevant else None,
            "mean_category_accuracy": round((min_hits + max_hits) / (2 * len(relevant)), 4) if relevant else None,
        }
    return result


def _probability_scores(taf: NativeTAF, observations: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in taf.groups:
        if group.probability is None or not _weather_has_event(group.state.weather, event):
            continue
        observed = any(
            group.start <= datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00")) < group.end
            and _weather_has_event(row["metar_text"], event)
            for row in observations
        )
        result.append({
            "issuance_utc": taf.issue_time.isoformat().replace("+00:00", "Z"),
            "event": event,
            "start_utc": group.start.isoformat().replace("+00:00", "Z"),
            "end_utc": group.end.isoformat().replace("+00:00", "Z"),
            "probability": group.probability,
            "observed_event": observed,
            "brier_component": round((group.probability - int(observed)) ** 2, 4),
        })
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _observations_in_validity(taf: NativeTAF, raw_metar: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in raw_metar
        if taf.valid_start <= datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00")) < taf.valid_end
    ]


def _has_complete_half_hour_coverage(taf: NativeTAF, observations: list[dict[str, Any]]) -> bool:
    """Require every half-hour sample before scoring a complete 24-hour TAF."""
    return _has_complete_cadence_coverage(taf, observations, timedelta(minutes=30))


def _has_complete_cadence_coverage(
    taf: NativeTAF,
    observations: list[dict[str, Any]],
    cadence: timedelta,
) -> bool:
    """Require every source-observation sample on the declared cadence."""
    if cadence <= timedelta(0):
        raise ValueError("Observation cadence must be positive")
    expected: set[datetime] = set()
    current = taf.valid_start
    while current < taf.valid_end:
        expected.add(current)
        current += cadence
    actual = {
        datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00"))
        for row in observations
    }
    return actual == expected


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    rain = summary["events"]["rain"]
    thunderstorm = summary["events"]["thunderstorm"]
    lines = [
        "# WAWP June 2026 Original TAF: TAF-Native Verification",
        "",
        "## Method",
        "",
        "This is an experimental, post-hoc verification of the original human TAFs against the June METAR archive. It follows a TAF-native interpretation: FM becomes prevailing at its stated time; BECMG is a transition range and then persists; TEMPO is a deterministic temporary-event window; PROB30/40 remains probabilistic. No operational forecast, database, or source workbook was modified.",
        "",
        "The primary rain and thunderstorm score has **no +/- timing grace**. A forecast event episode must overlap an observed event episode inside that TAF's validity. Each forecast episode can match at most one observed episode, preventing a long alert from receiving credit for several events.",
        "",
        "## Scope",
        "",
        f"- Standard official TAFs supplied: {summary['official_taf_count']}",
        f"- Complete-coverage official TAFs scored: {summary['scored_taf_count']}",
        f"- Nonstandard source TAFs reported but excluded: {summary['excluded_nonstandard_taf_count']}",
        f"- Official TAFs excluded for missing post-month METAR coverage: {summary['excluded_incomplete_taf_count']}",
        f"- Half-hourly METAR observations used: {summary['metar_count']}",
        f"- TAF-valid METAR samples: {summary['sample_count']}",
        "- Event definition: METAR/TAF textual `RA`, `SHRA`, or `TSRA` for rain; `TS*` for thunderstorm.",
        "",
        "## Primary Event-Episode Results",
        "",
        "| Event | Forecast episodes | Observed episodes | Hits | Misses | False alarms | POD | FAR | CSI | Frequency bias |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, values in (("Rain", rain["episode"]), ("Thunderstorm", thunderstorm["episode"])):
        def pct(value: Any) -> str:
            return "n/a" if value is None else f"{100 * value:.1f}%"
        lines.append(
            f"| {name} | {values['forecast_episodes']} | {values['observed_episodes']} | {values['hits']} | {values['misses']} | {values['false_alarms']} | {pct(values['POD'])} | {pct(values['FAR'])} | {pct(values['CSI'])} | {values['frequency_bias'] if values['frequency_bias'] is not None else 'n/a'} |"
        )
    lines.extend([
        "",
        "## Sample-Based Diagnostic",
        "",
        "This is a strict half-hourly yes/no diagnostic based on whether a deterministic TAF alert interval covers that exact observation time. It retains correct-dry samples, so accuracy is secondary and HSS is the more informative skill measure for rare events.",
        "",
        "| Event | Samples | Accuracy | POD | FAR | CSI | HSS |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for name, values in (("Rain", rain["sample"]), ("Thunderstorm", thunderstorm["sample"])):
        def pct(value: Any) -> str:
            return "n/a" if value is None else f"{100 * value:.1f}%"
        lines.append(f"| {name} | {values['sample_size']} | {pct(values['accuracy'])} | {pct(values['POD'])} | {pct(values['FAR'])} | {pct(values['CSI'])} | {values['HSS'] if values['HSS'] is not None else 'n/a'} |")
    lines.extend([
        "",
        "## Probability Groups",
        "",
        "`PROB30`/`PROB40` groups are not counted as deterministic alerts in the primary POD/FAR/CSI table. They are scored separately with Brier components. The sample is reported, but no skill score is claimed from a very small number of probability groups.",
        "",
        "## Categorical Visibility",
        "",
        "Visibility is verified by the minimum and maximum category seen in each hour, using all available half-hourly METARs and all TAF states that can apply during the hour. The category bounds are <150, 150-349, 350-599, 600-799, 800-1499, 1500-2999, 3000-4999, and >=5000 m.",
        "",
    ])
    for element, values in summary["ranges"].items():
        lines.append(f"- `{element}`: {values['eligible_hours']} eligible hours; min-category accuracy {values['minimum_category_accuracy'] * 100:.1f}%; max-category accuracy {values['maximum_category_accuracy'] * 100:.1f}%.")
    lines.extend([
        "",
        "## Interpretation Guardrails",
        "",
        "- This measures the historical human TAF archive, not an official score or an operational release decision.",
        "- A high dry-hour agreement cannot be used as evidence of good rare-event detection; use episode CSI/POD/FAR and sample HSS alongside it.",
        "- Gust verification is intentionally not promoted from twice-hourly METARs. The available observations do not provide a reliable continuous maximum-gust record.",
        "- One June month is a small sample. Results should guide the next multi-month verification, not establish permanent thresholds.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(workbook_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_metar, _, source_tafs = extract_june_workbook(workbook_path)
    official_hours = {"05:00", "11:00", "17:00", "23:00"}
    official_tafs = [row for row in source_tafs if row["issuance_utc"][11:16] in official_hours]
    excluded = [row for row in source_tafs if row not in official_tafs]
    scored_tafs: list[tuple[dict[str, Any], NativeTAF, list[dict[str, Any]]]] = []
    incomplete: list[dict[str, Any]] = []
    for source in official_tafs:
        taf = parse_native_taf(source["taf"])
        observations = _observations_in_validity(taf, raw_metar)
        if _has_complete_half_hour_coverage(taf, observations):
            scored_tafs.append((source, taf, observations))
        else:
            incomplete.append(source)
    event_rows: dict[str, list[dict[str, bool]]] = {"rain": [], "thunderstorm": []}
    episode_totals: dict[str, defaultdict[str, int]] = {"rain": defaultdict(int), "thunderstorm": defaultdict(int)}
    probability_rows: list[dict[str, Any]] = []
    range_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    sample_count = 0

    for source, taf, observations in scored_tafs:
        sample_count += len(observations)
        range_rows.extend(_range_rows(taf, observations))
        for event in ("rain", "thunderstorm"):
            forecast = forecast_event_intervals(taf, event)
            observed = observed_event_episodes(observations, event)
            counts = _interval_match_counts(forecast, observed)
            for key, value in counts.items():
                episode_totals[event][key] += value
            episode_rows.append({
                "issuance_utc": source["issuance_utc"],
                "event": event,
                **counts,
                "forecast_intervals_utc": json.dumps([[start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")] for start, end in forecast]),
                "observed_intervals_utc": json.dumps([[start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")] for start, end in observed]),
            })
            for observation in observations:
                at = datetime.fromisoformat(observation["observed_at_utc"].replace("Z", "+00:00"))
                sample = {
                    "forecast": any(start <= at < end for start, end in forecast),
                    "observed": _weather_has_event(observation["metar_text"], event),
                }
                event_rows[event].append(sample)
                sample_rows.append({
                    "issuance_utc": source["issuance_utc"],
                    "valid_time_utc": observation["observed_at_utc"],
                    "event": event,
                    "forecast_event": sample["forecast"],
                    "observed_event": sample["observed"],
                    "metar_text": observation["metar_text"],
                })
            probability_rows.extend(_probability_scores(taf, observations, event))

    probability_summary: dict[str, Any] = {}
    for event in ("rain", "thunderstorm"):
        relevant = [row for row in probability_rows if row["event"] == event]
        probability_summary[event] = {
            "group_count": len(relevant),
            "mean_brier_score": round(sum(row["brier_component"] for row in relevant) / len(relevant), 4) if relevant else None,
        }
    summary = {
        "scope": "June 2026 original human TAFs; TAF-native experimental verification; no operational changes",
        "method_version": "taf_native_v1",
        "workbook": str(workbook_path),
        "official_taf_count": len(official_tafs),
        "scored_taf_count": len(scored_tafs),
        "excluded_nonstandard_taf_count": len(excluded),
        "excluded_nonstandard_issuances": [row["issuance_utc"] for row in excluded],
        "excluded_incomplete_taf_count": len(incomplete),
        "excluded_incomplete_issuances": [row["issuance_utc"] for row in incomplete],
        "metar_count": len(raw_metar),
        "sample_count": sample_count,
        "events": {
            event: {
                "episode": _event_metrics(dict(episode_totals[event])),
                "sample": _sample_event_metrics(event_rows[event]),
            }
            for event in ("rain", "thunderstorm")
        },
        "probability_groups": probability_summary,
        "ranges": _summarize_ranges(range_rows),
    }
    _write_csv(output_dir / "june_native_event_episodes.csv", episode_rows)
    _write_csv(output_dir / "june_native_event_samples.csv", sample_rows)
    _write_csv(output_dir / "june_native_probability_groups.csv", probability_rows)
    _write_csv(output_dir / "june_native_visibility_ranges.csv", range_rows)
    (output_dir / "june_native_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_report(output_dir / "JUNE_NATIVE_VERIFICATION_REPORT.md", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path(r"C:\Users\MY ASUS\Downloads\Verifikasi TAF_FORM_Juni_2026 (2).xlsx"))
    parser.add_argument("--output-dir", type=Path, default=Path(r"D:\UJI_PERFORMA_MODEL\VERIFICATION_REPORTS\taf_june_native_verification_2026"))
    args = parser.parse_args()
    print(json.dumps(run(args.workbook, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
