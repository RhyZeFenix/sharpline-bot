"""
Bet tracking: CLV capture + automatic win/loss grading.

Every alert is logged to `tracked`. Each scan cycle the current sharp
consensus fair prob is written back to open rows — the last write
before the game locks is the closing line. CLV% = price * close_fair - 1
(i.e., your EV measured against the close instead of the open).

Grading uses The Odds API /scores endpoint:
  h2h     -> winner by final score
  totals  -> Over/Under vs combined score (push on exact)
  spreads -> team margin + handicap (push on exact)
  props   -> not auto-gradable (no player stats in the API)
"""

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from .scanner import Edge

log = logging.getLogger("sharpline.tracker")

GRADABLE = ("h2h", "totals", "spreads")


def _now() -> float:
    return time.time()


def grade(market: str, selection: str, home: str, away: str,
          home_score: float, away_score: float) -> Optional[str]:
    """Return 'win' | 'loss' | 'push' | None (ungradable)."""
    if market == "h2h":
        if home_score == away_score:
            return "push"
        winner = home if home_score > away_score else away
        return "win" if selection == winner else "loss"

    if market == "totals":
        m = re.match(r"^(Over|Under)\s+([\d.]+)$", selection)
        if not m:
            return None
        side, point = m.group(1), float(m.group(2))
        total = home_score + away_score
        if total == point:
            return "push"
        went_over = total > point
        return "win" if (side == "Over") == went_over else "loss"

    if market == "spreads":
        m = re.match(r"^(.+)\s+([+-][\d.]+)$", selection)
        if not m:
            return None
        team, point = m.group(1).strip(), float(m.group(2))
        if team == home:
            margin = home_score - away_score
        elif team == away:
            margin = away_score - home_score
        else:
            return None
        adj = margin + point
        if adj == 0:
            return "push"
        return "win" if adj > 0 else "loss"

    return None  # props / unknown


class Tracker:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS tracked (
                key TEXT PRIMARY KEY,
                ts REAL, sport TEXT, event TEXT, home TEXT, away TEXT,
                commence TEXT, market TEXT, selection TEXT, book TEXT,
                price REAL, fair_open REAL, ev_open REAL, stake REAL,
                fair_close REAL, clv_pct REAL,
                result TEXT, graded_ts REAL)"""
        )
        self.conn.commit()
        self._last_grade: Dict[str, float] = {}

    # ---------- logging ----------

    def record(self, edge: Edge):
        parts = edge.event.split(" @ ")
        away, home = (parts + ["?", "?"])[:2]
        self.conn.execute(
            """INSERT OR IGNORE INTO tracked
               (key, ts, sport, event, home, away, commence, market,
                selection, book, price, fair_open, ev_open, stake,
                fair_close, clv_pct, result, graded_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)""",
            (edge.key, _now(), edge.sport, edge.event, home, away,
             edge.commence, edge.market, edge.selection, edge.book,
             edge.odds, edge.fair_prob, edge.ev, edge.stake_units),
        )
        self.conn.commit()

    # ---------- CLV ----------

    def update_closes(self, event_label: str,
                      label_fairs: Dict[Tuple[str, str], float]):
        """Overwrite fair_close for open rows of this event with the
        latest consensus. Last write before lock = closing fair."""
        rows = self.conn.execute(
            "SELECT key, market, selection, price FROM tracked "
            "WHERE event = ? AND result IS NULL", (event_label,)
        ).fetchall()
        for key, market, selection, price in rows:
            p = label_fairs.get((market, selection))
            if p is None:
                continue
            clv = (price * p - 1.0) * 100.0
            self.conn.execute(
                "UPDATE tracked SET fair_close = ?, clv_pct = ? WHERE key = ?",
                (p, clv, key),
            )
        self.conn.commit()

    # ---------- grading ----------

    def sports_pending(self) -> list:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = self.conn.execute(
            "SELECT DISTINCT sport FROM tracked WHERE result IS NULL "
            "AND market IN (?,?,?) AND commence < ?",
            (*GRADABLE, now_iso),
        ).fetchall()
        # rate-limit grading to once per 30 min per sport
        return [s for (s,) in rows
                if _now() - self._last_grade.get(s, 0) > 1800]

    def grade_sport(self, sport: str, scores: list) -> int:
        """Grade pending rows against a /scores response. Returns #graded."""
        self._last_grade[sport] = _now()
        games = {}
        for g in scores:
            if not g.get("completed") or not g.get("scores"):
                continue
            sc = {s["name"]: float(s["score"]) for s in g["scores"]}
            games[(g.get("home_team"), g.get("away_team"))] = sc
        if not games:
            return 0

        rows = self.conn.execute(
            "SELECT key, home, away, market, selection FROM tracked "
            "WHERE sport = ? AND result IS NULL AND market IN (?,?,?)",
            (sport, *GRADABLE),
        ).fetchall()
        n = 0
        for key, home, away, market, selection in rows:
            sc = games.get((home, away))
            if not sc or home not in sc or away not in sc:
                continue
            res = grade(market, selection, home, away, sc[home], sc[away])
            if res is None:
                continue
            self.conn.execute(
                "UPDATE tracked SET result = ?, graded_ts = ? WHERE key = ?",
                (res, _now(), key),
            )
            n += 1
        self.conn.commit()
        if n:
            log.info("Graded %d bets for %s.", n, sport)
        return n

    # ---------- manual grading (props) ----------

    def pending(self, props_only: bool = False) -> list:
        """Ungraded rows, oldest first: (key, commence, ev_open, clv_pct)."""
        rows = self.conn.execute(
            "SELECT key, commence, ev_open, clv_pct FROM tracked "
            "WHERE result IS NULL ORDER BY commence"
        ).fetchall()
        if props_only:
            rows = [r for r in rows
                    if r[0].split("|")[1] not in GRADABLE]
        return rows

    def manual_grade(self, key_substring: str, result: str) -> int:
        """Grade rows whose key contains the substring. Returns #updated.
        result: win | loss | push | void (void = no bet, excluded from P/L)."""
        if result not in ("win", "loss", "push", "void"):
            raise ValueError(f"Bad result: {result}")
        rows = self.conn.execute(
            "SELECT key FROM tracked WHERE result IS NULL AND key LIKE ?",
            (f"%{key_substring}%",),
        ).fetchall()
        for (key,) in rows:
            self.conn.execute(
                "UPDATE tracked SET result = ?, graded_ts = ? WHERE key = ?",
                (result, _now(), key),
            )
        self.conn.commit()
        return len(rows)


def _cli():
    """
    Manual grading CLI (props aren't auto-gradable):
      python -m sharpline.tracker pending [props]
      python -m sharpline.tracker grade "<key substring>" win|loss|push|void
    """
    import sys
    args = sys.argv[1:]
    t = Tracker("alerts.db")
    if args[:1] == ["pending"]:
        rows = t.pending(props_only=(args[1:2] == ["props"]))
        if not rows:
            print("Nothing pending.")
            return
        for key, commence, ev, clv in rows:
            clv_s = f"{clv:+.2f}%" if clv is not None else "  n/a"
            print(f"  {commence}  EV {ev:+.2f}%  CLV {clv_s}  {key}")
        print(f"\n{len(rows)} pending. Grade with:\n"
              '  python -m sharpline.tracker grade "<key substring>" win|loss|push|void')
    elif args[:1] == ["grade"] and len(args) == 3:
        n = t.manual_grade(args[1], args[2])
        print(f"Graded {n} row(s) as {args[2]}."
              if n else f"No pending rows match {args[1]!r}.")
    else:
        print(_cli.__doc__)


if __name__ == "__main__":
    _cli()
