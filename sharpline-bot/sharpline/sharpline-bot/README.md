# SharpLine EV Bot

Scans every active sport on The Odds API and pings Discord whenever any soft book's price beats the sharp fair probability by your EV threshold. Dual-anchor consensus: **Pinnacle** anchors game lines (moneylines, spreads, totals); **FanDuel** anchors player props. A book is never swept in the class it anchors — FanDuel is still swept as a soft book on game lines.

## Why this strategy

Pinnacle takes sharp action at high limits and moves fast — its devigged line is the best free estimate of true probability that exists. Betting soft books only when they beat that line is the one retail strategy with a decades-long track record. Long-run edge lives in the 2–6% EV band; CLV against the Pinnacle close is your health metric.

## Run

```bash
pip install -r requirements.txt
export ODDS_API_KEY=xxx
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
python -m sharpline.main
```

Railway: add both env vars, start command `python -m sharpline.main`. Set `SCAN_PROPS=1` to enable props (1 API credit per event — watch your quota).

## Tuning (sharpline/config.py)

- `min_ev_pct` — 2.0 default. Raise to 3.0+ if you're getting more pings than you can bet.
- `devig_method` — `power` (default) corrects favorite-longshot bias; `worst_case` if you only want edges that survive both devig methods.
- `max_market_vig_pct` — skips markets where even Pinnacle is wide (fair prob unreliable).
- `min/max_fair_prob` — 15–72% band. Longshot EV is mostly devig noise; heavy favorites aren't worth limit risk.
- `my_books` — set to `("draftkings", "fanduel", ...)` so you only get pinged for books you hold.
- `sharp_weights` / `sharp_weights_props` — per-class anchor weights. Game lines: pinnacle .70 / betonlineag .20 / lowvig .10. Props: fanduel .60 / pinnacle .25 / betonlineag .15. Weights renormalize over whichever anchors price a given market; a prop with no FanDuel quote needs 2+ secondary sharps or it's skipped.
- `daily_report_hour_utc` — post the results report to Discord once a day at this UTC hour (-1 disables).
- `realert_ev_improvement` — same bet re-pings only if EV improved by this many points.

## Alert anatomy

Each ping shows: soft price vs fair price (decimal + American), fair win %, ¼-Kelly stake in units (1u = 1% bankroll), which sharp book anchored fair, and start time.

## Honest expectations

- Soft books limit winners. Expect stake limits within weeks if you hit these consistently.
- EV% is an estimate, not a guarantee — variance at +3% EV is brutal over hundreds of bets. Track CLV, not P/L, for the first 300+ bets.
- Same-point matching is strict by design: fair prob for -1.5 says nothing about -2.5. Missing "close" lines is correct behavior.

## Results tracking

Every alert is auto-logged. Game lines are auto-graded from the /scores endpoint and closing lines are captured each cycle for CLV. Props can't be auto-graded (no player stats in the API) — grade them by hand:

```bash
python -m sharpline.tracker pending props        # list ungraded props
python -m sharpline.tracker grade "Witt" win     # grade by key substring: win|loss|push|void
```

Reports split game lines (Pinnacle anchor) from props (FanDuel anchor), each with CLV, record, flat P/L, and ROI, plus a by-book table:

```bash
python -m sharpline.report                # terminal
python -m sharpline.report --discord      # also post to the webhook
```

The bot also posts this report to Discord automatically once a day (`daily_report_hour_utc`).

## Dual-source mode (SGO + Odds API)

Set both env vars and the bot switches modes automatically:
- `SGO_API_KEY` — SportsGameOdds: ALL soft books, exchanges, and player props. Swept every 15 min (`sweep_interval_sgo_min`). One object billed per event regardless of books/markets.
- `ODDS_API_KEY` — The Odds API: Pinnacle game lines ONLY (`bookmakers=pinnacle`, 3 credits/sport). Refreshed every 30 min (`sweep_interval_pinnacle_min`) and cached between refreshes; score-grading rides along.

Non-overlap is enforced in code on both sides: `pinnacle_odds()` strips everything except the anchor book and game-line markets from Odds API responses, and the SGO adapter drops any `pinnacle` entries and is the only source of props. Player props are auto-discovered from SGO oddIDs (any `{stat}-{playerID}-game-ou-*` market becomes `player_{stat}`) — no prop market list to maintain, and `SCAN_PROPS` is ignored in this mode.

If `SGO_API_KEY` is unset, the bot runs the original single-source Odds API mode unchanged.
