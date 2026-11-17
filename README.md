# gta6-twitch-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/paisalrvldoo/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/paisalrvldoo/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Deterministic GTA VI launch-day Twitch concurrent-viewer forecasting for make.fun.
Given the observed hour-1 average, predicts hours 2–24 at the 5th, 25th, 50th, 75th and 95th percentiles.

## Method

Weights comparable-launch analogs by their last 12 pre-launch hours' shape and hour-1 audience size. Aligns US daylight-saving time (DST) across launch dates, then blends weighted median launch ratios (50%) with a rolling-midnight console prior (50%).

The prior reflects regional midnight unlocks and evening viewing. The US unlocks together at midnight Eastern, **05:00Z** on the November launch date; Canada follows the same unlock.

We use `A=0.5` because Red Dead Redemption 2, Rockstar's last launch and also console-only with a shared US unlock at midnight Eastern, dipped less after hour 1 and rose more by the US afternoon than the Sony comparables, while GTA VI's midnight Eastern streams (Kai Cenat, Lacy and others) are US-centred.

Adds decaying intra-hour momentum from the final 15 minutes before the checkpoint. Uses log-normal quantiles with a wider upper tail and a horizon-growing log-spread floor, `0.10 + 0.10 * sqrt(hour - 1)`, adjusted for comparable dispersion and capped at `0.55`.

## Run

Python 3.13, standard library only; no dependencies to install.

```bash
MAKEFUN_INPUT_DIR=/path/to/input \
MAKEFUN_OUTPUT_DIR=/path/to/output \
python3 main.py
```

Reads JSON cases from `*.in` and writes one `<case_id>.out` JSON forecast per case. The input and output directories default to `/input` and `/output`.

## Backtest

Both tools require the kit's practice inputs and an external `practice_answers.json` file, which is **not included** in this repository. Its case IDs `p00`–`p03` map hour strings `"2"`–`"24"` to observed viewer counts.

```bash
python3 tools/backtest.py --input-dir /path/to/kit/input --answers /path/to/practice_answers.json
python3 tools/synthetic_gta.py --input-dir /path/to/kit/input --answers /path/to/practice_answers.json
```

Leave-one-launch-out mean log pinball loss, averaged per launch (lower is better):

| Launch group | Count | Forecast | Baseline |
| --- | ---: | ---: | ---: |
| All eligible | 26 | 0.277296 | 0.369485 |
| Rolling eligible | 12 | 0.258846 | 0.387772 |
| Four practice launches | 4 | 0.217717 | 0.387097 |

The synthetic mid scenario starts with a 700,000-viewer hour-1 mean. Its peak forecast median is **1,103,261 viewers** at **hour 21 (20:00–21:00 UTC)**. Generated scenarios stay outside the repo, by default in `/tmp/gta_syn`.

## Sources

- [SullyGnome](https://sullygnome.com/): hourly Twitch category averages via the kit's comparables and practice launches, including Red Dead Redemption 2, God of War Ragnarök, Marvel's Spider-Man 2 and Marvel's Wolverine launch days.
- [Rockstar Support](https://support.rockstargames.com/) / [PlayStation support](https://www.playstation.com/support/): midnight-local unlocks, with the US and Canada together at midnight Eastern.
- [gta6everything.com/unlock-times](https://gta6everything.com/unlock-times) and [Polygon](https://www.polygon.com/): regional unlock table.
- [NDTV](https://www.ndtv.com/) / [Sportskeeda](https://www.sportskeeda.com/): Kai Cenat, Lacy, Stable Ronaldo and IlloJuan launch-stream plans.
- [Streams Charts](https://streamscharts.com/), via [Yahoo](https://www.yahoo.com/) / [PlayStation LifeStyle](https://www.playstationlifestyle.net/): Valorant's 1.74M first-week record and GTA VI's 3.97M peak for the Aug 27 gameplay presentation.

<sub>Entered on [make.fun](https://make.fun/?ref=cy99dxdx&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
