"""Supabase mirror for the SharpLine website.

Every alert the bot sends to Discord is also inserted into Supabase,
where the Next.js site reads it live. Best-effort: any failure is
logged and never blocks alerting. Configure with env vars:

  SUPABASE_URL          e.g. https://abcd1234.supabase.co
  SUPABASE_SERVICE_KEY  the service_role key (server-side only — never
                        ship this to the browser; the site uses the
                        anon key with read-only RLS instead)

Run supabase_schema.sql in the Supabase SQL editor once to create the
table. Book categories drive the site's tabs.
"""

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("sharpline.supabase")

EXCHANGES = {"kalshi", "polymarket", "novig", "prophetx", "betopenly",
             "sporttrade", "matchbook", "betfairexchange"}
DFS_APPS = {"underdog", "sleeper", "prizepicks", "parlayplay", "dabble"}


def book_category(book: str) -> str:
    if book in EXCHANGES:
        return "exchange"
    if book in DFS_APPS:
        return "dfs"
    return "sportsbook"


class SupabaseWriter:
    def __init__(self, url: str, service_key: str, timeout: int = 10):
        self.enabled = bool(url and service_key)
        self.url = (url or "").rstrip("/")
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        self.timeout = timeout

    def insert_alert(self, edge) -> bool:
        if not self.enabled:
            return False
        row = {
            "key": edge.key,
            "sport": edge.sport,
            "event": edge.event,
            "commence": edge.commence,
            "market": edge.market,
            "market_class": ("prop" if edge.market.startswith(
                ("player_", "pitcher_", "batter_", "goalie_")) else "game_line"),
            "selection": edge.selection,
            "book": edge.book,
            "book_category": book_category(edge.book),
            "odds": round(edge.odds, 4),
            "fair_prob": round(edge.fair_prob, 5),
            "fair_odds": round(edge.fair_odds, 4),
            "ev_pct": round(edge.ev, 3),
            "stake_units": round(edge.stake_units, 2),
            "anchor": edge.anchor,
            "deeplink": edge.deeplink or None,
            "anchor_probs": edge.anchor_probs or None,
            "depth_note": edge.depth or None,
            "alerted_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            r = requests.post(
                f"{self.url}/rest/v1/alerts?on_conflict=key",
                json=row, headers=self.headers, timeout=self.timeout)
            if r.status_code in (200, 201, 204):
                return True
            log.warning("Supabase insert failed %s: %s",
                        r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("Supabase insert error: %s", e)
        return False

    def update_result(self, key: str, result: str,
                      clv_pct=None) -> bool:
        """Mirror grading so the site can show W/L + CLV on cards."""
        if not self.enabled:
            return False
        patch = {"result": result,
                 "graded_at": datetime.now(timezone.utc).isoformat()}
        if clv_pct is not None:
            patch["clv_pct"] = round(clv_pct, 3)
        try:
            r = requests.patch(
                f"{self.url}/rest/v1/alerts?key=eq.{requests.utils.quote(key, safe='')}",
                json=patch, headers=self.headers, timeout=self.timeout)
            return r.status_code in (200, 204)
        except requests.RequestException as e:
            log.warning("Supabase result update error: %s", e)
            return False
