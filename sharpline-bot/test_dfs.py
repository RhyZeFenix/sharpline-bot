"""DFS pick'em EV tests. Run: python test_dfs.py"""
from datetime import datetime, timedelta, timezone

from sharpline.config import Config
from sharpline.scanner import scan_event

CFG = Config()
COMMENCE = (datetime.now(timezone.utc) + timedelta(hours=5)) \
    .strftime("%Y-%m-%dT%H:%M:%SZ")


def _ou(desc, point, over, under):
    return [
        {"name": "Over", "description": desc, "point": point, "price": over},
        {"name": "Under", "description": desc, "point": point, "price": under},
    ]


EVENT = {
    "commence_time": COMMENCE,
    "home_team": "Kansas City Royals", "away_team": "Minnesota Twins",
    "bookmakers": [
        {   # FanDuel anchors the prop; devigged Over fair ~= 64%
            "key": "fanduel",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Kansas City Royals", "price": 1.91},
                    {"name": "Minnesota Twins", "price": 1.91}]},
                {"key": "player_battingBases",
                 "outcomes": _ou("Bobby Witt Jr.", 1.5, 1.52, 2.90)},
            ],
        },
        {   # Underdog: same line 1.5 -> leg edge vs 57.74% BE -> alert;
            # its synthetic price must NOT drive the EV math
            "key": "underdog",
            "markets": [
                {"key": "player_battingBases",
                 "outcomes": _ou("Bobby Witt Jr.", 1.5, 1.85, 1.85)},
                {"key": "h2h", "outcomes": [   # game line: must never alert
                    {"name": "Kansas City Royals", "price": 3.0}]},
            ],
        },
        {   # Sleeper: different line (2.5) -> strict same-point rule -> no alert
            "key": "sleeper",
            "markets": [
                {"key": "player_battingBases",
                 "outcomes": _ou("Bobby Witt Jr.", 2.5, 1.85, 1.85)},
            ],
        },
    ],
}


def test_dfs_path():
    edges = scan_event(EVENT, "baseball_mlb", CFG)
    by = {(e.book, e.market, e.selection): e for e in edges}

    ud = by.get(("underdog", "player_battingBases", "Bobby Witt Jr. Over 1.5"))
    assert ud, f"Underdog leg edge missing: {list(by)}"
    # entry math: odds field = multiplier, EV = p^2*3-1, depth explains it
    assert ud.odds == 3.0
    assert ud.fair_prob > 0.5774, "fair prob must beat 2-pick 3x breakeven"
    expect_ev = (ud.fair_prob ** 2 * 3.0 - 1.0) * 100.0
    assert abs(ud.ev - expect_ev) < 1e-6
    assert "2-pick 3x" in ud.depth and "leg BE 57.7%" in ud.depth
    assert ud.anchor == "fanduel"

    # Underdog Under at same line has fair ~36% -> below BE -> no alert
    assert ("underdog", "player_battingBases", "Bobby Witt Jr. Under 1.5") \
        not in by
    # Sleeper's 2.5 line has no same-point fair -> no alert
    assert not any(b == "sleeper" for b, _, _ in by)
    # DFS apps never alert on game lines
    assert not any(b == "underdog" and m == "h2h" for b, m, _ in by)


if __name__ == "__main__":
    test_dfs_path()
    print("test_dfs: all assertions passed")
