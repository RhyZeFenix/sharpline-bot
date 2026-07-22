"""SportsGameOdds v2 client + adapter.

Fetches /v2/events and normalizes each event into the same shape The
Odds API returns ({home_team, away_team, commence_time, bookmakers:
[{key, markets:[{key, outcomes:[...]}]}]}), so scanner.py needs zero
changes. SGO bills per event object regardless of books/markets pulled.

NON-OVERLAP GUARANTEE (SGO side): any `pinnacle` entry in byBookmaker
is dropped here — Pinnacle prices enter the pipeline ONLY via The Odds
API. (Rookie tier has no Pinnacle today; this guard keeps it true if
you ever upgrade to a tier that does.)
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

log = logging.getLogger("sharpline.sgo")

BASE = "https://api.sportsgameodds.com/v2"

# SGO bookmakerID -> the book keys the rest of the bot already uses
# (weights, fees, my_books are all keyed Odds-API-style).
BOOK_KEY_MAP = {
    "betonline": "betonlineag",
    "lowvig": "lowvig",
    "prophetexchange": "prophetx",   # SGO's ID for ProphetX
}

# Books whose prices must never come from SGO (anchor sourced elsewhere),
# plus SGO's aggregate 'unknown' book which can't be bet at.
EXCLUDED_SGO_BOOKS = {"pinnacle", "unknown"}


def _classify(odd: dict, sport_id: str, home: str, away: str, ev: dict):
    """Map an SGO odd to (market_key, outcome_name, description, point_field)
    or None to skip.

    Period rules: US sports price full-game markets at periodID 'game'.
    Soccer prices its MAIN markets (3-way ML, spread, total) at 'reg'
    and props at 'game'. Soccer's 2-way 'ml' is draw-no-bet — a different
    bet than Pinnacle's 3-way h2h on The Odds API — so it's skipped to
    keep the merged 'h2h' consensus apples-to-apples."""
    bet, side = odd.get("betTypeID"), odd.get("sideID") or ""
    stat, ent = odd.get("statID"), odd.get("statEntityID") or ""
    period = odd.get("periodID")
    player_id = odd.get("playerID")
    if not player_id and ent not in ("home", "away", "all", ""):
        player_id = ent  # older payloads: playerID only in statEntityID
    soccer = sport_id == "SOCCER"
    main_period = "reg" if soccer else "game"

    # --- player props (explicit playerID; ou lines and yes/no) ---
    if player_id:
        if period != "game":
            return None
        name = _player_name(ev, player_id)
        if bet == "ou" and side in ("over", "under"):
            return (f"player_{stat}", side.capitalize(), name, "overUnder")
        if bet == "yn" and side in ("yes", "no"):
            return (f"player_{stat}_yn", side.capitalize(), name, None)
        return None

    # --- game lines ---
    if period != main_period or stat != "points":
        return None
    if soccer:
        # 3-way ML maps onto Odds API's soccer h2h (Home/Away/Draw);
        # double-chance sides (home+draw etc.) and 2-way DNB 'ml' skipped
        if bet == "ml3way":
            if side == "home":
                return ("h2h", home, None, None)
            if side == "away":
                return ("h2h", away, None, None)
            if side == "draw":
                return ("h2h", "Draw", None, None)
            return None
    elif bet == "ml" and ent in ("home", "away"):
        return ("h2h", home if ent == "home" else away, None, None)
    if bet == "sp" and ent in ("home", "away"):
        return ("spreads", home if ent == "home" else away, None, "spread")
    if bet == "ou" and ent == "all":
        return ("totals", side.capitalize(), None, "overUnder")
    return None

# auth header per SGO's Postman collection
AUTH_HEADER = "x-api-key"


def american_to_decimal(a) -> Optional[float]:
    try:
        a = float(str(a).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return None


def _team_name(ev: dict, side: str) -> str:
    """Defensive extraction — SGO team objects carry a names dict."""
    t = (ev.get("teams") or {}).get(side) or {}
    names = t.get("names") or {}
    return (_first(names, "long", "medium", "short")
            or _first(t, "name", "displayName", "teamID")
            or side)


def _player_name(ev: dict, player_id: str) -> str:
    p = (ev.get("players") or {}).get(player_id) or {}
    name = _first(p, "name", "displayName")
    if name:
        return name
    fn, ln = p.get("firstName"), p.get("lastName")
    if fn or ln:
        return f"{fn or ''} {ln or ''}".strip()
    # "LEBRON_JAMES_NBA" -> "Lebron James" (last token is the league)
    parts = player_id.split("_")
    if len(parts) > 1:
        parts = parts[:-1]
    return " ".join(w.capitalize() for w in parts) or player_id


def _commence(ev: dict) -> Optional[str]:
    status = ev.get("status") or {}
    return (_first(status, "startsAt", "startTime")
            or _first(ev, "startsAt", "startTime", "commence_time"))


class SGOClient:
    def __init__(self, api_key: str, timeout: int = 20):
        self.api_key = api_key
        self.timeout = timeout
        self.objects_this_month = None  # populated if usage headers appear

    def _get(self, path: str, **params) -> dict:
        headers = {AUTH_HEADER: self.api_key}
        r = requests.get(f"{BASE}{path}", params=params,
                         headers=headers, timeout=self.timeout)
        if r.status_code == 429:
            log.warning("Rate limited by SGO.")
            return {}
        r.raise_for_status()
        return r.json() or {}

    def usage(self) -> Optional[str]:
        """Monthly object budget from /account/usage, e.g. '914/1000000'.
        Costs a request but no objects; logged after each sweep."""
        try:
            data = (self._get("/account/usage").get("data") or {})
            month = (data.get("rateLimits") or {}).get("per-month") or {}
            used, cap = month.get("current-entities"), month.get("max-entities")
            if used is not None:
                self.objects_this_month = used
                return f"{used}/{cap}"
        except Exception as e:
            log.debug("SGO usage check failed: %s", e)
        return None

    def events(self, league_ids: List[str], max_hours_ahead: float = 48.0,
               finalized: bool = False, lookback_hours: float = 40.0) -> List[dict]:
        """All events with odds inside the window, cursor-paginated.
        One object billed per event returned — books/markets are free.
        finalized=True instead returns completed events from the last
        lookback_hours (for grading), window-bounded so we never page
        through history."""
        now = datetime.now(timezone.utc)
        out: List[dict] = []
        if finalized:
            params = {
                "leagueID": ",".join(league_ids),
                "finalized": "true",
                "startsAfter": (now - timedelta(hours=lookback_hours))
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "startsBefore": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        else:
            params = {
                "leagueID": ",".join(league_ids),
                "oddsAvailable": "true",
                "startsAfter": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "startsBefore": (now + timedelta(hours=max_hours_ahead))
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        cursor = None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._get("/events", **params)
            out.extend(data.get("data") or [])
            cursor = data.get("nextCursor")
            if not cursor:
                break
        return out


def prop_scores(ev: dict) -> dict:
    """From a finalized SGO event, the actual stat value per player prop:
    {(event_label, market_key, player_name): score}. Drives auto-grading
    of over/under props (yn props stay manual)."""
    home, away = _team_name(ev, "home"), _team_name(ev, "away")
    label = f"{away} @ {home}"
    out = {}
    for odd in (ev.get("odds") or {}).values():
        pid = odd.get("playerID") or ""
        if not pid or odd.get("betTypeID") != "ou":
            continue
        score = odd.get("score")
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        out[(label, f"player_{odd.get('statID')}", _player_name(ev, pid))] = score
    return out


def normalize_event(ev: dict, league_map: Dict[str, str]) -> Optional[dict]:
    """SGO event -> Odds-API-shaped event dict (or None if unusable)."""
    commence = _commence(ev)
    if not commence:
        return None
    home = _team_name(ev, "home")
    away = _team_name(ev, "away")
    sport_id = ev.get("sportID") or ""

    # per-book accumulation: book -> market_key -> list of outcomes
    per_book: Dict[str, Dict[str, list]] = {}

    for odd in (ev.get("odds") or {}).values():
        if odd.get("started") or odd.get("ended") or odd.get("cancelled"):
            continue
        c = _classify(odd, sport_id, home, away, ev)
        if not c:
            continue
        mkey, name, desc, pt_field = c

        for raw_book, bo in (odd.get("byBookmaker") or {}).items():
            bkey = BOOK_KEY_MAP.get(raw_book, raw_book)
            if bkey in EXCLUDED_SGO_BOOKS:
                continue  # Pinnacle comes from The Odds API only
            if not bo.get("available"):
                continue
            dec = american_to_decimal(bo.get("odds"))
            if dec is None or dec <= 1.0:
                continue
            outcome = {"name": name, "price": dec}
            if bo.get("deeplink"):
                outcome["deeplink"] = bo["deeplink"]
            if desc:
                outcome["description"] = desc
            if pt_field:
                try:
                    outcome["point"] = float(bo.get(pt_field))
                except (TypeError, ValueError):
                    continue  # line missing -> can't same-point match
            per_book.setdefault(bkey, {}).setdefault(mkey, []).append(outcome)

    if not per_book:
        return None
    return {
        "id": ev.get("eventID"),
        "sport": league_map.get(ev.get("leagueID"), ev.get("leagueID", "")),
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {"key": b, "markets": [{"key": m, "outcomes": o}
                                   for m, o in mkts.items()]}
            for b, mkts in per_book.items()
        ],
    }


def norm_team(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def merge_pinnacle(sgo_event: dict, pinnacle_cache: dict,
                   tolerance_min: int = 30) -> dict:
    """Attach the cached Pinnacle bookmaker entry (from The Odds API)
    to a normalized SGO event, matching on teams + start time."""
    key = (norm_team(sgo_event["home_team"]), norm_team(sgo_event["away_team"]))
    hit = pinnacle_cache.get(key)
    if not hit:
        return sgo_event
    cached_commence, pinn_book = hit
    try:
        t_sgo = datetime.fromisoformat(
            sgo_event["commence_time"].replace("Z", "+00:00"))
        if abs((t_sgo - cached_commence).total_seconds()) > tolerance_min * 60:
            return sgo_event
    except (ValueError, TypeError):
        pass
    return {**sgo_event,
            "bookmakers": sgo_event["bookmakers"] + [pinn_book]}
