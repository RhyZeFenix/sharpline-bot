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

# Any Odds API market key with one of these prefixes is a player prop
# and gets the FanDuel-anchored consensus instead of the Pinnacle one.
PROP_PREFIXES = ("player_", "pitcher_", "batter_", "goalie_", "alternate_player_")


def is_prop(market_key: str) -> bool:
    return market_key.startswith(PROP_PREFIXES)


def weights_for(market_key: str, cfg: Config) -> dict:
    """Sharp weights for this market's class."""
    return cfg.sharp_weights_props if is_prop(market_key) else cfg.sharp_weights


def primary_for(market_key: str, cfg: Config) -> str:
    return cfg.sharp_book_props if is_prop(market_key) else cfg.sharp_book


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
    deeplink: str = ""      # direct add-to-betslip URL when the book provides one
    anchor_probs: dict = None  # per-anchor devigged fair prob, e.g. {"pinnacle": 0.63}

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
    """All fair probs one sharp book implies, keyed by (mkey, outcome_key).
    Only markets whose class this book anchors are included — e.g.
    FanDuel contributes prop fairs but never game-line fairs."""
    fairs: Dict[Tuple, float] = {}
    bkey = book.get("key")
    for mkt in book.get("markets", []):
        mkey = mkt["key"]
        if bkey not in weights_for(mkey, cfg):
            continue
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


def consensus_labels(event: dict, cfg: Config) -> Dict[Tuple[str, str], float]:
    """Weighted consensus fair prob keyed by (market, selection label).
    Used by the tracker to capture closing lines. Weights are chosen per
    market class: Pinnacle-anchored for game lines, FanDuel-anchored for
    props — so prop CLV is measured against the FanDuel-anchored close."""
    books = event.get("bookmakers", [])
    all_anchor_keys = set(cfg.sharp_weights) | set(cfg.sharp_weights_props)
    sharps = [b for b in books if b.get("key") in all_anchor_keys]
    if not sharps:
        return {}
    num: Dict[Tuple, float] = {}
    den: Dict[Tuple, float] = {}
    labels: Dict[Tuple, str] = {}
    for b in sharps:
        bkey = b["key"]
        for mkt in b.get("markets", []):
            mkey = mkt["key"]
            w = weights_for(mkey, cfg).get(bkey)
            if w is None:
                continue
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
                for o, p in zip(grp, devig(decs, cfg.devig_method)):
                    k = (mkey, _outcome_key(mkey, o))
                    num[k] = num.get(k, 0.0) + w * p
                    den[k] = den.get(k, 0.0) + w
                    labels[k] = _label(mkey, o)
    return {(k[0], labels[k]): num[k] / den[k] for k in num}


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

    # ---- gather every book that anchors ANY market class ----
    all_anchor_keys = set(cfg.sharp_weights) | set(cfg.sharp_weights_props)
    sharps = [b for b in books if b.get("key") in all_anchor_keys]
    if not sharps:
        return edges

    # ---- weighted consensus fair prob per (market, outcome) ----
    # _sharp_fairs already drops markets a book doesn't anchor, and the
    # weight is looked up per market class, so game lines blend the
    # Pinnacle-anchored set while props blend the FanDuel-anchored set.
    num: Dict[Tuple, float] = {}
    den: Dict[Tuple, float] = {}
    contributors: Dict[Tuple, List[str]] = {}
    comp: Dict[Tuple, dict] = {}   # per-anchor raw fair probs (site reweighting)
    for b in sharps:
        bkey = b["key"]
        for k, p in _sharp_fairs(b, cfg).items():
            w = weights_for(k[0], cfg)[bkey]
            num[k] = num.get(k, 0.0) + w * p
            den[k] = den.get(k, 0.0) + w
            contributors.setdefault(k, []).append(bkey)
            comp.setdefault(k, {})[bkey] = round(p, 5)
    consensus = {k: num[k] / den[k] for k in num}

    event_label = f"{event.get('away_team','?')} @ {event.get('home_team','?')}"

    # ---- sweep soft books ----
    for book in books:
        bkey = book.get("key")
        for mkt in book.get("markets", []):
            mkey = mkt["key"]
            # a book never gets swept in a market class it anchors —
            # FanDuel is excluded from prop sweeps but IS swept on
            # game lines (where it's just another soft book).
            if bkey in weights_for(mkey, cfg):
                continue
            if cfg.my_books and bkey not in cfg.my_books:
                continue
            min_ev = cfg.min_ev_pct_props if (is_props or is_prop(mkey)) \
                else cfg.min_ev_pct
            for so in mkt.get("outcomes", []):
                k = (mkey, _outcome_key(mkey, so))
                p_fair = consensus.get(k)
                if p_fair is None:
                    continue
                # require the class primary in this market's consensus,
                # OR at least two secondary sharps agreeing
                contribs = contributors.get(k, [])
                if primary_for(mkey, cfg) not in contribs and len(contribs) < 2:
                    continue
                if not (cfg.min_fair_prob <= p_fair <= cfg.max_fair_prob):
                    continue

                # ---- DFS pick'em apps: fixed-multiplier EV ----
                # Underdog/Sleeper/PrizePicks aren't priced books — their
                # "odds" are synthetic. A leg is +EV when the FanDuel-
                # anchored fair prob at the SAME line beats the per-leg
                # breakeven of the flagship entry: p_be = (1/mult)^(1/n).
                if bkey in (cfg.dfs_entries or {}):
                    if not is_prop(mkey):
                        continue  # pick'em apps are props only
                    n_picks, mult = cfg.dfs_entries[bkey]
                    p_be = (1.0 / mult) ** (1.0 / n_picks)
                    leg_edge = (p_fair - p_be) * 100.0
                    if leg_edge < cfg.min_dfs_leg_edge_pct:
                        continue
                    p_entry = p_fair ** n_picks
                    edges.append(Edge(
                        sport=sport,
                        event=event_label,
                        commence=commence,
                        market=mkey,
                        selection=_label(mkey, so),
                        book=bkey,
                        odds=mult,                      # entry payout multiplier
                        fair_prob=p_fair,
                        fair_odds=1.0 / p_fair,
                        ev=(p_entry * mult - 1.0) * 100.0,  # entry EV if all legs this strong
                        stake_units=kelly_units(
                            p_entry, mult, cfg.kelly_fraction,
                            cfg.bankroll_units),
                        anchor="+".join(contribs),
                        depth=(f"{n_picks}-pick {mult:g}x | leg BE "
                               f"{p_be * 100:.1f}% | leg edge {leg_edge:+.1f}%"),
                        deeplink=so.get("deeplink", ""),
                        anchor_probs=comp.get(k),
                    ))
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
                    anchor="+".join(contribs),
                    deeplink=so.get("deeplink", ""),
                    anchor_probs=comp.get(k),
                ))
    return edges
