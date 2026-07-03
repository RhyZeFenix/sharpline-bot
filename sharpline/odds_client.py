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

    def event_odds(self, sport: str, event_id: str, regions: str,
                   markets: str, odds_format: str) -> dict:
        return self._get(
            f"/sports/{sport}/events/{event_id}/odds",
            regions=regions, markets=markets, oddsFormat=odds_format,
        ) or {}
