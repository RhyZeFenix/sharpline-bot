"""
Kalshi order book depth.

Public (unauthenticated) market-data endpoints:
  GET /trade-api/v2/markets?status=open&cursor=...   (paginated)
  GET /trade-api/v2/markets/{ticker}/orderbook

Kalshi's book returns BIDS only — YES and NO are reciprocal, so:
  buying YES at <= X cents fills against NO bids priced >= (100 - X).

Flow when a kalshi edge fires:
  1. Fuzzy-match the edge (teams + selection) to an open market ticker
     using a cached market list (refreshed every CACHE_TTL seconds).
  2. Pull that ticker's orderbook and sum fillable contracts at or
     better than the flagged price.
  3. Return a human-readable depth note for the Discord embed.

Matching is best-effort: the matched market title is always included
in the note so you can eyeball that it's the right contract.
"""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger("sharpline.kalshi")

BASE = "https://external-api.kalshi.com/trade-api/v2"
CACHE_TTL = 600          # refresh open-market cache every 10 min
MAX_PAGES = 25           # pagination safety cap (1000 markets/page)
STOPWORDS = {"will", "the", "a", "an", "of", "at", "in", "on", "vs", "v",
              "to", "win", "beat", "game", "match", "over", "under", "or",
              "more", "than", "score", "total", "points", "runs", "goals"}


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9.]+", text.lower())
            if t not in STOPWORDS and len(t) > 1}


class KalshiDepth:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._markets: List[dict] = []
        self._fetched_at: float = 0.0

    # ---------- market cache ----------

    def _refresh_markets(self):
        if time.time() - self._fetched_at < CACHE_TTL and self._markets:
            return
        markets, cursor = [], None
        try:
            for _ in range(MAX_PAGES):
                params = {"status": "open", "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                r = requests.get(f"{BASE}/markets", params=params,
                                 timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                markets.extend(data.get("markets", []))
                cursor = data.get("cursor")
                if not cursor:
                    break
        except requests.RequestException as e:
            log.warning("Kalshi market fetch failed: %s", e)
            if not markets:
                return
        self._markets = markets
        self._fetched_at = time.time()
        log.info("Kalshi cache: %d open markets.", len(markets))

    # ---------- matching ----------

    def match(self, event_label: str, selection: str) -> Optional[dict]:
        """Best-effort match of an edge to a Kalshi market. Returns the
        market dict or None if nothing scores well enough."""
        self._refresh_markets()
        if not self._markets:
            return None
        want = _tokens(event_label) | _tokens(selection)
        best, best_score = None, 0.0
        for m in self._markets:
            title = f"{m.get('title','')} {m.get('subtitle','')} {m.get('yes_sub_title','')}"
            have = _tokens(title)
            if not have:
                continue
            overlap = len(want & have)
            score = overlap / max(1, len(want))
            if score > best_score:
                best, best_score = m, score
        if best is None or best_score < 0.4:
            return None
        return best

    # ---------- depth ----------

    def orderbook(self, ticker: str) -> Optional[dict]:
        try:
            r = requests.get(f"{BASE}/markets/{ticker}/orderbook",
                             timeout=self.timeout)
            r.raise_for_status()
            return r.json().get("orderbook") or {}
        except requests.RequestException as e:
            log.warning("Kalshi orderbook fetch failed for %s: %s", ticker, e)
            return None

    @staticmethod
    def fillable(book: dict, side: str, max_cost_cents: int) -> Tuple[int, float, Optional[int]]:
        """
        Contracts fillable buying `side` ('yes'|'no') at <= max_cost_cents.
        YES buys fill against NO bids at >= 100 - max_cost; NO buys fill
        against YES bids at >= 100 - max_cost.
        Returns (contracts, dollars_at_cost, best_ask_cents).
        """
        opp = "no" if side == "yes" else "yes"
        levels = book.get(opp) or []          # [[price_cents, qty], ...]
        contracts, dollars, best_ask = 0, 0.0, None
        for price, qty in levels:
            ask = 100 - price                 # cost of our side vs this bid
            if ask <= max_cost_cents:
                contracts += qty
                dollars += qty * ask / 100.0
                if best_ask is None or ask < best_ask:
                    best_ask = ask
        return contracts, dollars, best_ask

    # ---------- public entry ----------

    def depth_note(self, event_label: str, selection: str,
                   decimal_odds: float) -> str:
        """Human-readable depth summary for a kalshi edge, or ''. """
        m = self.match(event_label, selection)
        if not m:
            return "depth: no matching Kalshi market found"
        ticker = m.get("ticker", "?")
        book = self.orderbook(ticker)
        if book is None:
            return f"depth: {ticker} (orderbook unavailable)"

        max_cost = int(round(100.0 / decimal_odds))   # flagged price in cents
        # decide side: if the selection tokens overlap the YES subtitle, buy YES
        yes_side_text = f"{m.get('yes_sub_title','')} {m.get('title','')}"
        side = "yes" if _tokens(selection) & _tokens(yes_side_text) else "no"

        contracts, dollars, best_ask = self.fillable(book, side, max_cost)
        title = m.get("title", "")[:80]
        if contracts == 0:
            return (f"depth: 0 contracts ≤ {max_cost}¢ on {side.upper()} "
                    f"[{ticker}: {title}] — price likely moved")
        return (f"depth: {contracts} contracts (${dollars:,.0f}) fillable "
                f"≤ {max_cost}¢ on {side.upper()}, best ask {best_ask}¢ "
                f"[{ticker}: {title}]")
