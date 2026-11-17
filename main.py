"""Deterministic GTA VI launch-day Twitch quantiles; Python 3.13 stdlib only."""

import json
import math
import os
import re
from bisect import bisect_left
from datetime import date, datetime, timedelta
from pathlib import Path

# All model and output constants live here for subsequent tuning.
Z = (-1.645, -0.674, 0.0, 0.674, 1.645)
FIRST_HOUR = 2
LAST_HOUR = 24
BASELINE_SIGMA = 0.15
MIN_ANCHOR = 1.0
MIN_QUANTILE = 1e-9
MAX_QUANTILE = 1e9
PRE_HOURS = 12
FEATURE_MIN = -4.0
FEATURE_MAX = 2.0
HOUR_SECONDS = 3600
MAX_LAUNCH_RATIO = 15.0
ROLLING_TIER = 1.0
EARLY_ACCESS_TIER = 0.5
OTHER_TIER = 0.7
SKIP_UNLOCK = re.compile(
    r"\b(?:simultaneous|global|PC[ -]+only|PC[ -]+single|"
    r"F2P|free[ -]to[ -]play|live[ -]service)\b", re.IGNORECASE
)
NEGATED_UNLOCK = re.compile(
    r"\b(?:no|not|never|without|neither)\s+"
    r"(?:(?:a|an|any|single|fully|truly)\s+)*"
    + SKIP_UNLOCK.pattern
    + r"(?:\s*(?:/|and|or|nor)\s*" + SKIP_UNLOCK.pattern + r")*",
    re.IGNORECASE,
)
BW = 0.5
SIZE_BW = 1.5
MIN_SHARED_FEATURES = 3
DEFAULT_DISTANCE = 1.0
A = 0.5
FLOOR0 = 0.10
FLOOR1 = 0.08
K_SD = 0.8
SIGMA_CAP = 1.2
UP_SKEW = 1.1
PRIOR = (
    0.92, 0.85, 0.85, 0.95, 1.45, 1.50, 1.40, 1.25,
    1.10, 1.00, 0.95, 0.95, 1.05, 1.20, 1.40, 1.60,
    1.80, 1.95, 2.05, 2.05, 1.95, 1.80, 1.60,
)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hour_offset(value: str, day_start: datetime) -> int:
    return math.floor((parse_time(value) - day_start).total_seconds() / HOUR_SECONDS)


def dst(day: date) -> int:
    """US daylight time under the specified second-March/first-November rule."""
    march = date(day.year, 3, 8)
    november = date(day.year, 11, 1)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    return int(start <= day < end)


def log_viewers(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite history viewers")
    return math.log(max(MIN_ANCHOR, value))


def pre_features(case: dict, day_start: datetime, p1: float) -> dict[int, float]:
    """Hourly pre-launch means relative to the target's hour-1 print."""
    rows = case.get("history_hourly") or []
    time_key = "from"
    if not rows:
        rows = case.get("history") or []
        time_key = "at"
    buckets = {}
    for row in rows:
        offset = hour_offset(row[time_key], day_start)
        if -PRE_HOURS <= offset < 0:
            value = float(row["viewers"])
            if not math.isfinite(value):
                raise ValueError("Non-finite history viewers")
            buckets.setdefault(-offset, []).append(value)
    log_anchor = math.log(p1)
    return {
        k: min(FEATURE_MAX, max(FEATURE_MIN,
               log_viewers(math.fsum(values) / len(values)) - log_anchor))
        for k, values in buckets.items()
    }


def interpolated_log(logs: dict[int, float], offsets: list[int], hour: int) -> float | None:
    """Interpolate only inside the comparable's observed hourly range."""
    if hour in logs:
        return logs[hour]
    pos = bisect_left(offsets, hour)
    if pos == 0 or pos == len(offsets):
        return None
    left, right = offsets[pos - 1], offsets[pos]
    fraction = (hour - left) / (right - left)
    return logs[left] + fraction * (logs[right] - logs[left])


def comparable_data(case: dict, day_start: datetime) -> list[dict]:
    comparables = []
    for comp in case.get("comparables") or []:
        unlock = comp.get("unlock") or ""
        # Remove only negated descriptors; an affirmative elsewhere still excludes.
        affirmative_unlock = NEGATED_UNLOCK.sub("", unlock)
        if "before release" in comp["name"].lower() or SKIP_UNLOCK.search(affirmative_unlock):
            continue
        comp_start = parse_time(comp["day_start"])
        logs = {
            hour_offset(row["from"], comp_start): log_viewers(row["viewers"])
            for row in comp.get("hourly") or []
        }
        if 0 not in logs:
            continue
        offsets = sorted(logs)
        launch_logs = [
            value for h in range(1, LAST_HOUR)
            if (value := interpolated_log(logs, offsets, h)) is not None
        ]
        if launch_logs and max(launch_logs) - logs[0] > math.log(MAX_LAUNCH_RATIO):
            continue
        shift = dst(comp_start.date()) - dst(day_start.date())
        # Anchors must be observed; interpolation fills interior features/ratios only.
        if -shift not in logs:
            continue
        anchor = logs[-shift]
        features = {
            k: value - anchor for k in range(1, PRE_HOURS + 1)
            if (value := interpolated_log(logs, offsets, -k - shift)) is not None
        }
        ratios = {
            h: value - anchor for h in range(FIRST_HOUR, LAST_HOUR + 1)
            if (value := interpolated_log(logs, offsets, h - 1 - shift)) is not None
        }
        unlock_lower = unlock.lower()
        if "midnight local" in unlock_lower or "rolling" in unlock_lower:
            tier = ROLLING_TIER
        elif "early access" in unlock_lower or "early-access" in unlock_lower:
            tier = EARLY_ACCESS_TIER
        else:
            tier = OTHER_TIER
        comparables.append({"anchor": anchor, "features": features,
                            "ratios": ratios, "tier": tier})
    return comparables


def weight_comparables(comparables: list[dict], features: dict[int, float], p1: float) -> None:
    log_anchor = math.log(p1)
    for comp in comparables:
        shared = sorted(features.keys() & comp["features"].keys())
        if len(shared) < MIN_SHARED_FEATURES:
            distance_squared = DEFAULT_DISTANCE ** 2
        else:
            recency = [1 / math.sqrt(k) for k in shared]
            distance_squared = math.fsum(
                w * (comp["features"][k] - features[k]) ** 2
                for k, w in zip(shared, recency)
            ) / math.fsum(recency)
        size_difference = comp["anchor"] - log_anchor
        comp["log_weight"] = (
            math.log(comp["tier"]) - distance_squared / (2 * BW ** 2)
            - size_difference ** 2 / (2 * SIZE_BW ** 2)
        )


def weighted_summary(comparables: list[dict], hour: int) -> tuple[float, float] | None:
    available = [comp for comp in comparables if hour in comp["ratios"]]
    if not available:
        return None
    # A common rescaling preserves weighted statistics and prevents underflow.
    max_log_weight = max(comp["log_weight"] for comp in available)
    values = sorted(
        (comp["ratios"][hour], math.exp(comp["log_weight"] - max_log_weight))
        for comp in available
    )
    total_weight = math.fsum(w for _, w in values)
    cumulative = 0.0
    median = values[-1][0]
    for value, weight in values:
        cumulative += weight
        if cumulative >= total_weight / 2:
            median = value
            break
    mean = math.fsum(value * weight for value, weight in values) / total_weight
    sd = math.sqrt(math.fsum(weight * (value - mean) ** 2
                             for value, weight in values) / total_weight)
    return median, sd


def first_viewers(case: dict) -> float:
    value = float(case["known"][0]["viewers"])
    if not math.isfinite(value):
        raise ValueError("Non-finite hour-1 print")
    return max(MIN_ANCHOR, value)


def valid_quantiles(values: list[float]) -> list[float]:
    if len(values) != len(Z) or any(not math.isfinite(q) for q in values):
        raise ValueError("Invalid quantiles")
    return sorted(min(MAX_QUANTILE, max(MIN_QUANTILE, q)) for q in values)


def baseline_forecast(p1: float) -> dict:
    hours = []
    for hour in range(FIRST_HOUR, LAST_HOUR + 1):
        spread = BASELINE_SIGMA * math.sqrt(hour - 1)
        # Bound in log space so even an extreme finite anchor cannot overflow.
        q = [math.exp(min(math.log(MAX_QUANTILE), math.log(p1) + z * spread)) for z in Z]
        hours.append({"hour": hour, "q": valid_quantiles(q)})
    return {"hours": hours}


def forecast_case(case: dict) -> dict:
    p1 = first_viewers(case)
    day_start = parse_time(case["day_start"])
    features = pre_features(case, day_start, p1)
    comparables = comparable_data(case, day_start)
    weight_comparables(comparables, features, p1)
    prior_shift = dst(day_start.date())
    log_anchor = math.log(p1)
    hours = []
    for hour in range(FIRST_HOUR, LAST_HOUR + 1):
        prior_index = min(hour - FIRST_HOUR + prior_shift, len(PRIOR) - 1)
        prior_log = math.log(PRIOR[prior_index])
        summary = weighted_summary(comparables, hour)
        if summary is None:
            median_log, sd = prior_log, 0.0
        else:
            comp_median, sd = summary
            median_log = A * comp_median + (1 - A) * prior_log
        sigma = min(SIGMA_CAP, max(FLOOR0 + FLOOR1 * math.sqrt(hour - 1), K_SD * sd))
        q = [math.exp(min(math.log(MAX_QUANTILE),
                         log_anchor + median_log + z * sigma * (UP_SKEW if z > 0 else 1)))
             for z in Z]
        hours.append({"hour": hour, "q": valid_quantiles(q)})
    return {"hours": hours}


def write_forecast(output_dir: Path, case_id: str, forecast: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(forecast, separators=(",", ":"), allow_nan=False) + "\n"
    (output_dir / f"{case_id}.out").write_text(text, encoding="utf-8")


def main() -> None:
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
    for path in sorted(input_dir.glob("*.in")):
        case = {}
        case_id = path.stem
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
            candidate = case.get("id", path.stem)
            if isinstance(candidate, str) and candidate and Path(candidate).name == candidate:
                case_id = candidate
            forecast = forecast_case(case)
            write_forecast(output_dir, case_id, forecast)
        except Exception:
            # Bad metadata or model inputs must not prevent the other cases running.
            try:
                p1 = first_viewers(case)
            except Exception:
                p1 = MIN_ANCHOR
            try:
                write_forecast(output_dir, case_id, baseline_forecast(p1))
            except OSError:
                # No forecast can be written to an unavailable output directory.
                continue


if __name__ == "__main__":
    main()
