"""
SharpLine EV Bot — configuration.

Strategy: Pinnacle-anchored positive EV.
  1. Pull odds for every in-season sport from The Odds API.
  2. For each two-sided market Pinnacle prices, remove the vig -> fair prob.
  3. Compare every soft book's price against fair. EV% = p_fair * dec - 1.
  4. Ping Discord when EV >= threshold and sanity filters pass.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- API ---
    odds_api_key: str = ""                  # set via ODDS_API_KEY env var
    regions: str = "us,us2,eu,us_ex"        # eu = Pinnacle anchor; us_ex = Kalshi/Novig/Polymarket/ProphetX
    markets: str = "h2h,spreads,totals"     # core markets scanned every cycle
    odds_format: str = "decimal"

    # --- Sharp anchor ---
    sharp_book: str = "pinnacle"          # primary sharp (required unless 2+ others)
    # weighted consensus: fair prob = weighted avg across sharps present
    sharp_weights: dict = None            # set in __post_init__ below
    devig_method: str = "power"             # "multiplicative" | "power" | "worst_case"

    # --- Edge thresholds ---
    min_ev_pct: float = 2.0                 # alert at >= +2.0% EV vs fair
    min_ev_pct_props: float = 3.5           # props are noisier; demand more edge
    max_fair_prob: float = 0.72             # skip heavy favorites (limits/low payout)
    min_fair_prob: float = 0.48             # only alert when fair win% >= 48%
    max_market_vig_pct: float = 8.0         # if sharp market is this wide, fair prob is junk
    realert_ev_improvement: float = 1.0     # re-ping same bet only if EV improved by 1pt
    max_hours_to_start: float = 48.0        # skip games further out (stale-line mirages)
    min_hours_to_start: float = 0.0         # 0 = allow up to lock; no live betting

    # --- Staking (suggestion only) ---
    kelly_fraction: float = 0.25            # quarter Kelly
    bankroll_units: float = 100.0           # stake shown in units of 1% bankroll

    # --- Props (credit-heavy: 1 request per event) ---
    scan_props: bool = False
    prop_markets: str = (
        "player_points,player_rebounds,player_assists,"
        "pitcher_strikeouts,batter_total_bases,"
        "player_shots_on_goal,player_goal_scorer_anytime"
    )
    max_prop_events_per_cycle: int = 10     # cap credit burn

    # --- Ops / credit control ---
    # ONLY scan these sports (empty tuple = all active sports = expensive).
    # Find keys at /v4/sports. These 6 cover your main edges.
    include_sports: tuple = (
        "baseball_mlb", "soccer_fifa_world_cup",
        "basketball_wnba", "icehockey_nhl",
        "americanfootball_nfl", "basketball_nba",
    )
    daily_credit_budget: int = 600          # ~20K/month plan spread evenly
    credits_floor: int = 500                # hard-stop scanning below this
    poll_seconds: int = 120                 # minimum sleep; budget pacing may extend it
    sports_refresh_cycles: int = 30         # re-fetch /sports list every N cycles
    exclude_sports: tuple = ("politics",)
    discord_webhook_url: str = ""           # set via DISCORD_WEBHOOK_URL env var
    db_path: str = "alerts.db"
    request_timeout: int = 20

    # Books you actually hold accounts at. Empty = alert on every book.
    my_books: tuple = ("kalshi", "novig", "polymarket", "prophetx")

    # EV haircut (percentage points) per book to account for exchange
    # fees/commission. VERIFY current fee schedules and adjust.
    book_fee_pct: dict = None

    def __post_init__(self):
        if self.sharp_weights is None:
            object.__setattr__(self, "sharp_weights",
                               {"pinnacle": 0.70, "betonlineag": 0.20, "lowvig": 0.10})
        if self.book_fee_pct is None:
            object.__setattr__(self, "book_fee_pct",
                               {"kalshi": 1.5, "polymarket": 0.0,
                                "novig": 0.0, "prophetx": 0.0, "betopenly": 1.0})
