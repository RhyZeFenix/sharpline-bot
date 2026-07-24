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
    # --- API: The Odds API (Pinnacle ONLY when SGO is configured) ---
    odds_api_key: str = ""                  # set via ODDS_API_KEY env var
    odds_api_bookmakers: str = "pinnacle"   # dual-source mode: bookmakers param (1-10 books = 1 region-equivalent -> 3 credits/sport)
    regions: str = "us,us2,eu,us_ex"        # legacy single-source mode only (no SGO key set)
    markets: str = "h2h,spreads,totals"     # NON-OVERLAP: Odds API is game lines only; props NEVER fetched here
    odds_format: str = "decimal"

    # --- API: SportsGameOdds (everything except Pinnacle) ---
    sgo_api_key: str = ""                   # set via SGO_API_KEY env var; empty = legacy single-source mode
    sgo_leagues: tuple = ("MLB", "WNBA", "NBA", "NFL", "NHL")
    # SGO leagueID -> Odds API sport key, so score-grading keeps working
    sgo_league_map: dict = None             # set in __post_init__

    # --- Supabase mirror (website feed; empty = disabled) ---
    supabase_url: str = ""                  # set via SUPABASE_URL env var
    supabase_service_key: str = ""          # set via SUPABASE_SERVICE_KEY env var

    # --- DFS pick'em apps ---
    # app -> (picks, payout multiplier) for the flagship entry used in
    # EV math. VERIFY these against each app's current payout tables —
    # they change. Legs alert when fair prob beats (1/mult)^(1/picks)
    # by at least min_dfs_leg_edge_pct.
    dfs_entries: dict = None                # set in __post_init__
    min_dfs_leg_edge_pct: float = 2.0       # fair prob - breakeven, in points

    # --- Sweep cadences (dual-source mode) ---
    sweep_interval_sgo_min: float = 15.0    # soft books + props sweep
    sweep_interval_pinnacle_min: float = 30.0  # sharp anchor refresh

    # --- Sharp anchors (dual): game lines vs player props ---
    # Pinnacle is sharpest on game lines (ML/spreads/totals); FanDuel is
    # sharpest on player props. Each market class gets its own weighted
    # consensus. A book that anchors a class is never swept as a soft
    # book *within that class* (FanDuel still gets swept on game lines).
    sharp_book: str = "pinnacle"            # game-line primary (required unless 2+ others)
    sharp_book_props: str = "fanduel"       # prop primary (required unless 2+ others)
    sharp_weights: dict = None              # game lines; set in __post_init__
    sharp_weights_props: dict = None        # props; set in __post_init__
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
    discord_results_webhook_url: str = ""   # DISCORD_RESULTS_WEBHOOK_URL; results/reports channel (falls back to main)
    book_webhooks_json: str = ""            # BOOK_WEBHOOKS_JSON: {"book": "webhook url", ...} per-book channels
    daily_report_hour_utc: int = 13         # post tracker summary to Discord daily at this UTC hour (-1 = off)
    db_path: str = "alerts.db"
    request_timeout: int = 20

    # Books you actually hold accounts at. Empty = alert on every book.
    my_books: tuple = ("fanduel", "draftkings", "betmgm", "caesars",
                   "espnbet", "fanatics", "betrivers", "hardrockbet",
                   "bet365", "fliff",
                   "novig", "prophetx", "kalshi", "polymarket", "sporttrade",
                   "underdog", "sleeper", "prizepicks")

    # EV haircut (percentage points) per book to account for exchange
    # fees/commission. VERIFY current fee schedules and adjust.
    book_fee_pct: dict = None

    def __post_init__(self):
        if self.dfs_entries is None:
            # 2-pick power entries paying 3x -> leg breakeven 57.74%
            object.__setattr__(self, "dfs_entries", {
                "underdog": (2, 3.0),
                "sleeper": (2, 3.0),
                "prizepicks": (2, 3.0),
            })
        if self.sgo_league_map is None:
            object.__setattr__(self, "sgo_league_map", {
                "MLB": "baseball_mlb", "WNBA": "basketball_wnba",
                "NBA": "basketball_nba", "NFL": "americanfootball_nfl",
                "NHL": "icehockey_nhl",
            })
        if self.sharp_weights is None:
            # Pinnacle-led game-line consensus. Circa contributes nothing
            # until the SGO plan carries it (weights renormalize over the
            # anchors actually present). Exchanges are secondaries only:
            # thin liquidity + commission make them noisy references.
            # Sporttrade is deliberately NOT an anchor so it stays
            # sweepable for alerts (US-bettable, thinnest liquidity).
            object.__setattr__(self, "sharp_weights", {
                "pinnacle": 0.55, "circa": 0.15, "bookmakereu": 0.12,
                "betfairexchange": 0.10, "matchbook": 0.08})
        if self.sharp_weights_props is None:
            # FanDuel-anchored props; Circa joins on Pro.
            object.__setattr__(self, "sharp_weights_props",
                               {"fanduel": 0.55, "circa": 0.25, "pinnacle": 0.20})
        if self.book_fee_pct is None:
            object.__setattr__(self, "book_fee_pct",
                               {"kalshi": 1.5, "polymarket": 0.0,
                                "novig": 0.0, "prophetx": 0.0, "betopenly": 1.0})
