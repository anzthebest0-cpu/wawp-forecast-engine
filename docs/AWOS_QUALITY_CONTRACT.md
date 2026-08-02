# AWOS Cross-Grain Quality Contract

Status: Batch 10 observe-only quality artifact.

## Authoritative Inputs

- Hourly aviation wind uses the first `WD36/WS36` pair, the 10-minute wind.
- Hourly rainfall uses final-column `RA36`, scaled by ten.
- Hourly rain timestamps are interval-ending.
- One-minute rain is a rolling one-hour accumulation snapshot. It must never be
  summed as minute increments.
- Hourly gust is the maximum one-minute `WGS` in `(hour-1h, hour]`.
- Missing one-minute WGS is unknown, not a zero-knot gust. Hourly
  `wind_gust_max` remains null unless at least one populated WGS value exists in
  its interval-ending hour.

## Quality Products

`awos_quality.json` reports:

- hourly coverage, missing timestamps, duplicates, freshness, and hard ranges;
- recent minute-day completeness and populated gust coverage;
- minute/hour gust-max agreement;
- exact-boundary minute/hour rain agreement;
- whether verification evidence is currently usable.

The artifact is observe-only. A `blocked` status freezes new
verification-driven learning but does not rewrite observations. Missing periods
or sparse gust evidence produce warnings and remain visible for human review.

Eligibility is also parameter-specific. Core hourly parameters can remain
eligible while gust verification is blocked for insufficient populated WGS
evidence.
