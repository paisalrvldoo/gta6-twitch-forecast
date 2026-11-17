"""Build three minute-level GTA VI scenarios and run the entry on each."""

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.backtest import ANSWERS_PATH, INPUT_DIR, load_pool, model, read_json


DAY_START = "2026-11-19T00:00:00Z"
CHECKPOINT = "2026-11-19T01:00:00Z"
PRE_RELEASE = 30_000
SCENARIOS = {
    "low": (150_000, 250_000, 300_000),
    "mid": (350_000, 550_000, 700_000),
    "high": (700_000, 1_100_000, 1_400_000),
}


def utc_text(at) -> str:
    return at.isoformat().replace("+00:00", "Z")


def rounded_mean(values: list[int]) -> int:
    return math.floor(math.fsum(values) / len(values) + 0.5)


def scenario_viewers(minute: int, levels: tuple[int, int, int]) -> int:
    """Linear ramps after NZ unlock; the last segment fixes the minute mean."""
    level18, level23, h1 = levels
    if minute <= 11 * 60:
        value = PRE_RELEASE
    elif minute <= 18 * 60:
        value = PRE_RELEASE + (level18 - PRE_RELEASE) * (minute - 11 * 60) / (7 * 60)
    elif minute <= 23 * 60:
        value = level18 + (level23 - level18) * (minute - 18 * 60) / (5 * 60)
    else:
        # Hour 1's 00:00..00:59 reads average at 00:29:30, 89.5 min after 23:00.
        value = level23 + (h1 - level23) * (minute - 23 * 60) / 89.5
    return math.floor(value + 0.5)


def build_case(template: dict, pool: list[dict], name: str,
               levels: tuple[int, int, int]) -> dict:
    day_start = model.parse_time(DAY_START)
    history_start = day_start - timedelta(days=1)
    history = [
        {"at": utc_text(history_start + timedelta(minutes=minute)),
         "viewers": scenario_viewers(minute, levels), "channels": None}
        for minute in range(25 * 60)
    ]
    # Like h00, history ends strictly before the checkpoint; no future reads.
    hourly = [
        {"from": history[offset]["at"],
         "viewers": rounded_mean([row["viewers"] for row in history[offset:offset + 60]]),
         "channels": None, "reads": 60}
        for offset in range(0, len(history), 60)
    ]
    if hourly[-1]["viewers"] != levels[2]:
        raise ValueError("Synthetic hour-1 mean differs from its scenario")
    return {
        **template,
        "id": f"h00_{name}",
        "game": {"name": "Grand Theft Auto VI", "twitch_id": "1867279146"},
        "day_start": DAY_START,
        "checkpoint": CHECKPOINT,
        "known": [{"hour": 1, **hourly[-1], "until": CHECKPOINT}],
        "forecast": [
            {"hour": hour,
             "from": utc_text(day_start + timedelta(hours=hour - 1)),
             "until": utc_text(day_start + timedelta(hours=hour))}
            for hour in range(model.FIRST_HOUR, model.LAST_HOUR + 1)
        ],
        "history": history,
        "history_hourly": hourly,
        "comparables": pool,
        "streams": None,
        "categories_hourly": None,
        "notes": (f"Synthetic {name} GTA VI scenario: rolling midnight local, "
                  "NZ unlock 2026-11-18T11:00Z; piecewise-linear minute reads. "
                  "Channels unknown. Comparable pool loaded as in tools/backtest.py."),
    }


def print_forecast(name: str, case: dict, forecast: dict) -> None:
    p1 = model.first_viewers(case)
    momentum = model.first_hour_momentum(case, p1)
    print(f"\n{name.upper()}: hour-1 mean={p1:,.0f}; momentum g={momentum:.6f}")
    print(f"{'Hour':>4} {'UTC interval':>13} {'Median':>12} {'5%':>12} {'95%':>12}")
    for row in forecast["hours"]:
        hour = row["hour"]
        low, _, median, _, high = row["q"]
        interval = f"{hour - 1:02d}:00–{hour % 24:02d}:00"
        print(f"{hour:>4} {interval:>13} {median:>12,.0f} {low:>12,.0f} {high:>12,.0f}")
    peak = max(forecast["hours"], key=lambda row: row["q"][2])
    print(f"Peak forecast median: hour {peak['hour']} "
          f"({peak['hour'] - 1:02d}:00–{peak['hour'] % 24:02d}:00 UTC), "
          f"{peak['q'][2]:,.0f} viewers")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path, default=ANSWERS_PATH,
                        help="External practice answers JSON, read in place")
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR,
                        help="Kit practice cases p00..p03")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/gta_syn"),
                        help="External directory for generated .in files and out/*.out")
    args = parser.parse_args()
    if args.output_dir.resolve().is_relative_to(REPO):
        parser.error("Generated cases contain answer-derived comparables; keep them outside the repo")
    pool = load_pool(args.input_dir, args.answers)
    template = read_json(args.input_dir / "p00.in")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = {}
    for name, levels in SCENARIOS.items():
        case = build_case(template, pool, name, levels)
        cases[name] = case
        (args.output_dir / f"{case['id']}.in").write_text(
            json.dumps(case, separators=(",", ":"), allow_nan=False) + "\n",
            encoding="utf-8",
        )
    forecasts_dir = args.output_dir / "out"
    subprocess.run(
        [sys.executable, str(REPO / "main.py")], check=True, timeout=8,
        env={**os.environ, "MAKEFUN_INPUT_DIR": str(args.output_dir.resolve()),
             "MAKEFUN_OUTPUT_DIR": str(forecasts_dir.resolve())},
    )
    print(f"Pool: {len(pool)} launches, including all four practice launches.")
    print(f"Inputs: {args.output_dir}/*.in; forecasts: {forecasts_dir}/*.out")
    for name, case in cases.items():
        forecast = read_json(forecasts_dir / f"{case['id']}.out")
        if forecast != model.forecast_case(case):
            raise ValueError(f"Entry used fallback output for {name}")
        print_forecast(name, case, forecast)


if __name__ == "__main__":
    main()
