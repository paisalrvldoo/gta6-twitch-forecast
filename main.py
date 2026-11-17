"""Deterministic GTA VI launch-day Twitch quantiles; Python 3.13 stdlib only."""

import json
import math
import os
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
    return baseline_forecast(first_viewers(case))


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
