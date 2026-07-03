"""
Scanner v2: weighted multi-sharp consensus.

For each event:
  1. Find every sharp book present (pinnacle, betonlineag, lowvig).
  2. Devig each sharp's market -> fair probs per outcome.
  3. Blend into a weighted consensus fair prob.
  4. Sweep every soft book; alert where price beats consensus fair by
     the EV threshold.

Matching is strict: spreads/totals/props only compare identical points.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .devig import devig, ev_pct, kelly_units, overround
from .config import Config

log = logging.getLogger("sharpline.scanner")


@dataclass
class Edge:
    sport: str
    event: str
    commence: str
    market: str
    selection: str
    book: str
    odds: float
    fair_prob: float
    fair_odds: float
    ev: float
    stake_units: float
    anchor: str
    depth: str = ""

    @property
    def key(self) -> str:
        return f"{self.event}|{self.market}|{self.selection}|{self.book}"


def _outcome_key(market_key: str, o: dict) -> Tuple:
    name = o.get("name", "")
    desc = o.get("description", "")
    point = o.get("point")
    if market_key == "h2h":
        return (name,)
    return (name, desc, point)


def _label(market_key: str, o: dict) -> str:
    name = o.get("name", "")
    desc = o.get("description", "")
    point = o.get("point")
    if market_key == "h2h":
        return name
    if desc:
        return f"{desc} {name} {point}"
    if point is not None:
        return f"{name} {point:+g}" if market_key == "spreads" else f"{name} {point}"
    return name


def _group_key(mkey: str, o: dict) -> Tuple:
    if mkey == "h2h":
        return ("h2h",)
    if mkey == "spreads":
        return ("spreads", abs(o.get("point") or 0))
    return (mkey, o.get("description", ""), o.get("point"))


def _hours_to_start(commence_iso: str) -> Optional[float]:
    try:
        t = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
        return (t - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except (ValueError, AttributeError):
        return None


def _sharp_fairs(book: dict, cfg: Config) -> Dict[Tuple, float]:
    """All fair probs one sharp book implies, keyed by (mkey, outcome_key)."""
    fairs: Dict[Tuple, float] = {}
    for mkt in book.get("markets", []):
        mkey = mkt["key"]
        groups: Dict[Tuple, List[dict]] = {}
        for o in mkt.get("outcomes", []):
            groups.setdefault(_group_key(mkey, o), []).append(o)
        for grp in groups.values():
            if len(grp) < 2:
                continue
            decs = [o["price"] for o in grp]
            vig = (overround(decs) - 1.0) * 100.0
            if vig > cfg.max_market_vig_pct or vig < -0.5:
                continue
            probs = devig(decs, cfg.devig_method)
            for o, p in zip(grp, probs):
                fairs[(mkey, _outcome_key(mkey, o))] = p
    return fairs


def scan_event(event: dict, sport: str, cfg: Config,
               is_props: bool = False) -> List[Edge]:
    edges: List[Edge] = []
    books = event.get("bookmakers", [])
    if not books:
        return edges

    # ---- time filter: skip stale far-out markets ----
    commence = event.get("commence_time", "")
    hrs = _hours_to_start(commence)
    if hrs is not None and (hrs > cfg.max_hours_to_start or hrs < cfg.min_hours_to_start):
        return edges

    # ---- gather sharp anchors present ----
    sharps = [(b, cfg.sharp_weights[b["key"]])
              for b in books if b.get("key") in cfg.sharp_weights]
    if not sharps:
        return edges
    sharp_keys = {b["key"] for b, _ in sharps}
    # require the primary sharp OR at least two secondaries
    if cfg.sharp_book not in sharp_keys and len(sharps) < 2:
        return edges

    # ---- weighted consensus fair prob per (market, outcome) ----
    num: Dict[Tuple, float] = {}
    den: Dict[Tuple, float] = {}
    contributors: Dict[Tuple, List[str]] = {}
    for b, w in sharps:
        for k, p in _sharp_fairs(b, cfg).items():
            num[k] = num.get(k, 0.0) + w * p
            den[k] = den.get(k, 0.0) + w
            contributors.setdefault(k, []).append(b["key"])
    consensus = {k: num[k] / den[k] for k in num}

    event_label = f"{event.get('away_team','?')} @ {event.get('home_team','?')}"
    min_ev = cfg.min_ev_pct_props if is_props else cfg.min_ev_pct

    # ---- sweep soft books ----
    for book in books:
        bkey = book.get("key")
        if bkey in sharp_keys:
            continue
        if cfg.my_books and bkey not in cfg.my_books:
            continue
        for mkt in book.get("markets", []):
            mkey = mkt["key"]
            for so in mkt.get("outcomes", []):
                k = (mkey, _outcome_key(mkey, so))
                p_fair = consensus.get(k)
                if p_fair is None:
                    continue
                if not (cfg.min_fair_prob <= p_fair <= cfg.max_fair_prob):
                    continue
                price = so["price"]
                e = ev_pct(p_fair, price) - cfg.book_fee_pct.get(bkey, 0.0)
                if e < min_ev:
                    continue
                edges.append(Edge(
                    sport=sport,
                    event=event_label,
                    commence=commence,
                    market=mkey,
                    selection=_label(mkey, so),
                    book=bkey,
                    odds=price,
                    fair_prob=p_fair,
                    fair_odds=1.0 / p_fair,
                    ev=e,
                    stake_units=kelly_units(
                        p_fair, price, cfg.kelly_fraction, cfg.bankroll_units),
                    anchor="+".join(contributors[k]),
                ))
    return edges
