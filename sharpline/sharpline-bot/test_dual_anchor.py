"""Dual-anchor routing tests: Pinnacle anchors game lines, FanDuel anchors props.

Run: python test_dual_anchor.py
"""
from datetime import datetime, timedelta, timezone

from sharpline.config import Config
from sharpline.scanner import scan_event, is_prop, weights_for, primary_for

CFG = Config()
COMMENCE = (datetime.now(timezone.utc) + timedelta(hours=5)) \
    .strftime("%Y-%m-%dT%H:%M:%SZ")


def _mkt(key, outcomes):
    return {"key": key, "outcomes": outcomes}


def _ou(desc, point, over, under):
    return [
        {"name": "Over", "description": desc, "point": point, "price": over},
        {"name": "Under", "description": desc, "point": point, "price": under},
    ]


EVENT = {
    "commence_time": COMMENCE,
    "home_team": "Royals", "away_team": "Twins",
    "bookmakers": [
        {   # game-line anchor; prices a prop FanDuel also prices (secondary)
            # and one prop FanDuel does NOT price (should never alert alone)
            "key": "pinnacle",
            "markets": [
                _mkt("h2h", [{"name": "Royals", "price": 1.91},
                             {"name": "Twins", "price": 1.91}]),
                _mkt("batter_total_bases", _ou("B. Witt Jr.", 1.5, 1.87, 1.95)),
                _mkt("pitcher_strikeouts", _ou("C. Ragans", 6.5, 1.90, 1.92)),
            ],
        },
        {   # prop anchor; soft book on game lines
            "key": "fanduel",
            "markets": [
                _mkt("h2h", [{"name": "Royals", "price": 2.05},   # +EV vs Pinnacle fair
                             {"name": "Twins", "price": 1.75}]),
                _mkt("batter_total_bases", _ou("B. Witt Jr.", 1.5, 1.87, 1.95)),
            ],
        },
        {   # pure soft book
            "key": "draftkings",
            "markets": [
                _mkt("h2h", [{"name": "Royals", "price": 2.10},   # +EV vs Pinnacle fair
                             {"name": "Twins", "price": 1.70}]),
                _mkt("batter_total_bases", _ou("B. Witt Jr.", 1.5, 2.15, 1.65)),  # Over +EV vs FD fair
                _mkt("pitcher_strikeouts", _ou("C. Ragans", 6.5, 2.30, 1.55)),    # juicy but no FD anchor
            ],
        },
    ],
}


def test_helpers():
    assert is_prop("batter_total_bases") and is_prop("player_points")
    assert not is_prop("h2h") and not is_prop("spreads")
    assert weights_for("h2h", CFG) is CFG.sharp_weights
    assert weights_for("player_points", CFG) is CFG.sharp_weights_props
    assert primary_for("h2h", CFG) == "pinnacle"
    assert primary_for("player_points", CFG) == "fanduel"


def test_routing():
    edges = scan_event(EVENT, "baseball_mlb", CFG)
    by = {(e.book, e.market, e.selection): e for e in edges}

    # 1. DK game line swept against Pinnacle-anchored fair
    dk_ml = by.get(("draftkings", "h2h", "Royals"))
    assert dk_ml, "DK Royals ML edge missing"
    assert "pinnacle" in dk_ml.anchor and "fanduel" not in dk_ml.anchor, \
        f"game-line anchor wrong: {dk_ml.anchor}"

    # 2. FanDuel IS swept on game lines (soft there)
    assert ("fanduel", "h2h", "Royals") in by, \
        "FanDuel should be sweepable on game lines"

    # 3. DK prop swept against FanDuel-anchored fair
    dk_prop = by.get(("draftkings", "batter_total_bases", "B. Witt Jr. Over 1.5"))
    assert dk_prop, "DK prop edge missing"
    assert "fanduel" in dk_prop.anchor, f"prop anchor wrong: {dk_prop.anchor}"

    # 4. FanDuel never alerted on a prop (it anchors that class)
    assert not any(b == "fanduel" and is_prop(m) for b, m, _ in by), \
        "FanDuel must not be swept on props"

    # 5. Pinnacle-only prop (no FanDuel quote, single contributor) never alerts
    assert not any(m == "pitcher_strikeouts" for _, m, _ in by), \
        "prop without the FanDuel primary (and <2 sharps) must not alert"

    # 6. Pinnacle itself never swept on game lines
    assert not any(b == "pinnacle" for b, _, _ in by)


if __name__ == "__main__":
    test_helpers()
    test_routing()
    print("test_dual_anchor: all assertions passed")
