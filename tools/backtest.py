"""Leave-one-launch-out evaluation using the forecast and filter in main.py."""

import argparse
import json
import math
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as model


INPUT_DIR = Path("/workspace/gta6/kit/gta6-twitch-ccu-forecast-01-kit/input")
ANSWERS_PATH = Path("/workspace/gta6/research/practice_answers.json")
PRACTICE_IDS = ("p00", "p01", "p02", "p03")
LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
GRID = {
    "A": (0.3, 0.5, 0.7, 0.9),
    "BW": (0.3, 0.5, 0.8),
    "K_SD": (0.6, 0.8, 1.0),
    "FLOOR1": (0.05, 0.08, 0.12),
    "UP_SKEW": (1.0, 1.1, 1.25),
}


@dataclass
class Target:
    name: str
    case: dict
    actual: dict[int, float]
    eligible: bool
    rolling: bool
    practice_id: str | None


@dataclass
class Score:
    target: Target
    forecast: float
    baseline: float


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checked_viewers(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Viewers must be finite and nonnegative")
    return value


def load_pool(input_dir: Path, answers_path: Path) -> list[dict]:
    """Read external answers in place; hour 1 overrides its duplicate in history."""
    pool = list(read_json(input_dir / "p00.in")["comparables"])
    answers = read_json(answers_path)
    for case_id in PRACTICE_IDS:
        case = read_json(input_dir / f"{case_id}.in")
        start = model.parse_time(case["day_start"])
        hourly = {
            model.hour_offset(row["from"], start): dict(row)
            for row in case["history_hourly"]
        }
        hourly[0] = dict(case["known"][0])
        for hour_text, value in answers[case_id].items():
            hour = int(hour_text)
            if not model.FIRST_HOUR <= hour <= model.LAST_HOUR:
                raise ValueError(f"Invalid answer hour {hour_text!r} for {case_id}")
            hourly[hour - 1] = {
                "from": (start + timedelta(hours=hour - 1)).isoformat(),
                "viewers": checked_viewers(value),
            }
        pool.append({
            "name": case["game"]["name"],
            "day_start": case["day_start"],
            "unlock": case["notes"],
            "practice_id": case_id,
            "hourly": [hourly[offset] for offset in sorted(hourly)],
        })
    return pool


def make_targets(pool: list[dict]) -> list[Target]:
    targets = []
    for index, launch in enumerate(pool):
        start = model.parse_time(launch["day_start"])
        # Filtering a launch against its own date tests eligibility with no DST shift.
        eligible = bool(model.comparable_data({"comparables": [launch]}, start))
        practice_id = launch.get("practice_id")
        if not eligible and practice_id is None:
            continue
        hourly = {
            model.hour_offset(row["from"], start): row
            for row in launch["hourly"]
        }
        actual = {
            offset + 1: checked_viewers(row["viewers"])
            for offset, row in hourly.items()
            if model.FIRST_HOUR <= offset + 1 <= model.LAST_HOUR
        }
        if not actual:
            raise ValueError(f"No scored hours for {launch['name']}")
        case = {
            "day_start": launch["day_start"],
            "known": [{"hour": 1, **hourly[0]}],
            "history_hourly": [hourly[h] for h in sorted(hourly) if h < 0],
            "comparables": pool[:index] + pool[index + 1:],
        }
        unlock = (launch.get("unlock") or "").lower()
        targets.append(Target(
            launch["name"], case, actual, eligible,
            "midnight local" in unlock or "rolling" in unlock, practice_id,
        ))
    return targets


def pinball_loss(forecast: dict, actual: dict[int, float]) -> float:
    """Rules' mean log pinball loss over observed hours and all five levels."""
    predictions = {row["hour"]: row["q"] for row in forecast["hours"]}
    losses = []
    for hour, viewers in sorted(actual.items()):
        log_y = math.log(max(1.0, viewers))
        for tau, q in zip(LEVELS, predictions[hour], strict=True):
            d = log_y - math.log(q)
            losses.append(2 * max(tau * d, (tau - 1) * d))
    return math.fsum(losses) / len(losses)


def evaluate(targets: list[Target]) -> list[Score]:
    return [Score(
        target,
        pinball_loss(model.forecast_case(target.case), target.actual),
        pinball_loss(model.baseline_forecast(model.first_viewers(target.case)),
                     target.actual),
    ) for target in targets]


@contextmanager
def temporary_settings(settings: dict[str, float]):
    """Patch imported model globals for one evaluation, restoring them even on error."""
    previous = {name: getattr(model, name) for name in settings}
    try:
        for name, value in settings.items():
            setattr(model, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(model, name, value)


def format_settings(settings: dict[str, float]) -> str:
    return ", ".join(f"{name}={value:g}" for name, value in settings.items())


def search_grid(targets: list[Target]) -> dict[str, float]:
    rolling = [t for t in targets if t.eligible and t.rolling]
    if not rolling:
        raise ValueError("Grid search requires at least one eligible rolling launch")
    best = {name: getattr(model, name) for name in GRID}
    cache = {}

    def objective(settings: dict[str, float]) -> float:
        key = tuple(settings[name] for name in GRID)
        if key not in cache:
            with temporary_settings(settings):
                losses = [pinball_loss(model.forecast_case(t.case), t.actual) for t in rolling]
            cache[key] = math.fsum(losses) / len(losses)
        return cache[key]

    best_loss = objective(best)
    print("\nTwo-pass coordinate search, minimizing rolling-eligible mean:")
    for pass_number in range(1, 3):
        for name, values in GRID.items():
            for value in values:
                candidate = {**best, name: value}
                loss = objective(candidate)
                if loss < best_loss:
                    best, best_loss = candidate, loss
        print(f"Pass {pass_number}: rolling={best_loss:.6f}; {format_settings(best)}")
    print(f"Best settings: {format_settings(best)}")
    print(f"Best rolling loss: {best_loss:.6f}; {len(cache)} distinct settings evaluated")
    return best


def print_scores(scores: list[Score]) -> None:
    print(f"{'Launch':<48} {'Hours':>5} {'Forecast':>10} {'Baseline':>10}  Groups")
    for score in scores:
        target = score.target
        groups = ["all"] if target.eligible else ["excluded by filter"]
        if target.eligible and target.rolling:
            groups.append("rolling")
        if target.practice_id:
            groups.append(target.practice_id)
        print(f"{target.name:<48} {len(target.actual):>5} "
              f"{score.forecast:>10.6f} {score.baseline:>10.6f}  {', '.join(groups)}")
    print("\nMean of per-launch losses:")
    groups = (
        ("All eligible", [s for s in scores if s.target.eligible]),
        ("Rolling eligible", [s for s in scores if s.target.eligible and s.target.rolling]),
        ("Four practice launches", [s for s in scores if s.target.practice_id]),
    )
    for label, subset in groups:
        if not subset:
            print(f"{label}: n=0, no scored launches")
            continue
        forecast = math.fsum(s.forecast for s in subset) / len(subset)
        baseline = math.fsum(s.baseline for s in subset) / len(subset)
        print(f"{label}: n={len(subset)}, forecast={forecast:.6f}, baseline={baseline:.6f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path, default=ANSWERS_PATH,
                        help="External practice answers JSON (never copied into the repo)")
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR,
                        help="Directory containing kit practice cases p00..p03")
    parser.add_argument("--grid", action="store_true",
                        help="Search two coordinate passes over model constants for rolling launches")
    args = parser.parse_args()
    started = time.perf_counter()
    pool = load_pool(args.input_dir, args.answers)
    targets = make_targets(pool)
    print(f"Pool: {len(pool)} launches; each target uses the other {len(pool) - 1}.")
    print(f"Current settings: {format_settings({name: getattr(model, name) for name in GRID})}")
    print_scores(evaluate(targets))
    if args.grid:
        best = search_grid(targets)
        print("\nEvaluation with best grid settings (temporary overrides):")
        with temporary_settings(best):
            print_scores(evaluate(targets))
    print(f"\nElapsed: {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()
