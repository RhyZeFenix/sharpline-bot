"""Thin client for The Odds API v4 with credit tracking."""

import logging
import requests

log = logging.getLogger("sharpline.odds")

BASE = "https://api.the-odds-api.com/v4"


class OddsClient:
    def __init__(self, api_key: str, timeout: int = 20):
        self.api_key = api_key
        self.timeout = timeout
        self.credits_remaining = None
        self.credits_used = None

    def _get(self, path: str, **params):
        params["apiKey"] = self.api_key
        r = requests.get(f"{BASE}{path}", params=params, timeout=self.timeout)
        self.credits_remaining = r.headers.get("x-requests-remaining")
        self.credits_used = r.headers.get("x-requests-used")
        if r.status_code == 429:
            log.warning("Rate limited by Odds API.")
            return None
        if r.status_code == 422:
            # market not offered for this sport — normal, skip quietly
            return None
        r.raise_for_status()
        return r.json()

    def active_sports(self, exclude: tuple = ()) -> list:
        data = self._get("/sports") or []
        return [
            s["key"] for s in data
            if s.get("active") and not s.get("has_outrights")
            and not any(x in s["key"] for x in exclude)
        ]

    def events(self, sport: str) -> list:
        """FREE endpoint (0 credits): upcoming events for a sport."""
        return self._get(f"/sports/{sport}/events") or []

    def odds(self, sport: str, regions: str, markets: str, odds_format: str) -> list:
        return self._get(
            f"/sports/{sport}/odds",
            regions=regions, markets=markets, oddsFormat=odds_format,
        ) or []

    GAME_LINE_MARKETS = ("h2h", "spreads", "totals")

    def pinnacle_odds(self, sport: str, markets: str, odds_format: str,
                      bookmakers: str = "pinnacle") -> list:
        """Dual-source mode: fetch ONLY the sharp anchor's game lines.
        Cost: 3 credits/sport (1-10 books = 1 region-equivalent x 3 markets).
        NON-OVERLAP (Odds API side): response is filtered to the anchor
        book(s) and to game-line markets — props can never enter the
        pipeline from this source even if the request params change."""
        events = self._get(
            f"/sports/{sport}/odds",
            bookmakers=bookmakers, markets=markets, oddsFormat=odds_format,
        ) or []
        allowed = set(bookmakers.split(","))
        for ev in events:
            ev["bookmakers"] = [
                {**b, "markets": [m for m in b.get("markets", [])
                                  if m.get("key") in self.GAME_LINE_MARKETS]}
                for b in ev.get("bookmakers", []) if b.get("key") in allowed
            ]
        return events

    def scores(self, sport: str, days_from: int = 3) -> list:
        """Completed game scores (costs 2 credits per call)."""
        return self._get(f"/sports/{sport}/scores", daysFrom=days_from) or []

    def event_odds(self, sport: str, event_id: str, regions: str,
                   markets: str, odds_format: str) -> dict:
        return self._get(
            f"/sports/{sport}/events/{event_id}/odds",
            regions=regions, markets=markets, oddsFormat=odds_format,
        ) or {}
