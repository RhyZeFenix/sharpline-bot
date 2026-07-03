# SharpLine EV Bot

Scans every active sport on The Odds API, devigs Pinnacle's market to get a no-vig fair probability, and pings Discord whenever any soft book's price beats fair by your EV threshold. Moneylines, spreads, totals by default; player props optional.

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
- `realert_ev_improvement` — same bet re-pings only if EV improved by this many points.

## Alert anatomy

Each ping shows: soft price vs fair price (decimal + American), fair win %, ¼-Kelly stake in units (1u = 1% bankroll), which sharp book anchored fair, and start time.

## Honest expectations

- Soft books limit winners. Expect stake limits within weeks if you hit these consistently.
- EV% is an estimate, not a guarantee — variance at +3% EV is brutal over hundreds of bets. Track CLV, not P/L, for the first 300+ bets.
- Same-point matching is strict by design: fair prob for -1.5 says nothing about -2.5. Missing "close" lines is correct behavior.
