"""SGO adapter + dual-source merge tests. Run: python test_sgo.py"""
from datetime import datetime, timedelta, timezone

from sharpline.config import Config
from sharpline.scanner import scan_event, is_prop
from sharpline.sgo_client import (american_to_decimal, normalize_event,
                                  merge_pinnacle, norm_team)

CFG = Config()
T0 = datetime.now(timezone.utc) + timedelta(hours=5)
COMMENCE = T0.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bb(**books):  # byBookmaker builder
    return {b: dict(v, available=v.get("available", True))
            for b, v in books.items()}


SGO_EVENT = {
    "eventID": "evt123",
    "leagueID": "MLB",
    "status": {"startsAt": COMMENCE},
    "teams": {"home": {"names": {"long": "Kansas City Royals"}},
              "away": {"names": {"long": "Minnesota Twins"}}},
    "players": {"BOBBY_WITT_JR_MLB": {"name": "Bobby Witt Jr."}},
    "links": {"bookmakers": {
        "draftkings": "https://sportsbook.draftkings.com/event/123",
        "betonline": "https://sports.betonline.ag/game/456"}},
    "odds": {
        # game lines — FanDuel is SOFT here; pinnacle entry must be dropped
        "points-home-game-ml-home": {
            "statID": "points", "statEntityID": "home", "periodID": "game",
            "betTypeID": "ml", "sideID": "home",
            "byBookmaker": _bb(
                fanduel={"odds": "+105"},          # 2.05 dec, +EV vs fair .5
                draftkings={"odds": "+110"},       # 2.10 dec
                betonline={"odds": "-105"},        # remap -> betonlineag
                pinnacle={"odds": "-110"},         # MUST be dropped
            )},
        "points-away-game-ml-away": {
            "statID": "points", "statEntityID": "away", "periodID": "game",
            "betTypeID": "ml", "sideID": "away",
            "byBookmaker": _bb(fanduel={"odds": "-125"},
                               draftkings={"odds": "-130"})},
        # totals with per-book line
        "points-all-game-ou-over": {
            "statID": "points", "statEntityID": "all", "periodID": "game",
            "betTypeID": "ou", "sideID": "over",
            "byBookmaker": _bb(
                draftkings={"odds": "-110", "overUnder": "8.5"})},
        # player prop — FanDuel anchors, DK is soft and mispriced
        "battingBases-BOBBY_WITT_JR_MLB-game-ou-over": {
            "statID": "battingBases", "statEntityID": "BOBBY_WITT_JR_MLB",
            "playerID": "BOBBY_WITT_JR_MLB",
            "periodID": "game", "betTypeID": "ou", "sideID": "over",
            "byBookmaker": _bb(
                fanduel={"odds": "-107", "overUnder": "1.5"},
                draftkings={"odds": "+115", "overUnder": "1.5"})},
        "battingBases-BOBBY_WITT_JR_MLB-game-ou-under": {
            "statID": "battingBases", "statEntityID": "BOBBY_WITT_JR_MLB",
            "playerID": "BOBBY_WITT_JR_MLB",
            "periodID": "game", "betTypeID": "ou", "sideID": "under",
            "byBookmaker": _bb(
                fanduel={"odds": "-102", "overUnder": "1.5"},
                draftkings={"odds": "-145", "overUnder": "1.5"})},
        # 1st-half market must be skipped
        "points-home-1h-ml-home": {
            "statID": "points", "statEntityID": "home", "periodID": "1h",
            "betTypeID": "ml", "sideID": "home",
            "byBookmaker": _bb(fanduel={"odds": "+100"})},
        # unavailable odds must be skipped
        "points-home-game-sp-home": {
            "statID": "points", "statEntityID": "home", "periodID": "game",
            "betTypeID": "sp", "sideID": "home",
            "byBookmaker": {"fanduel": {"odds": "-110", "spread": "-1.5",
                                        "available": False}}},
    },
}

SOCCER_EVENT = {
    "eventID": "evtSoccer", "sportID": "SOCCER", "leagueID": "BUNDESLIGA",
    "status": {"startsAt": COMMENCE},
    "teams": {"home": {"names": {"long": "1. FC Union Berlin"}},
              "away": {"names": {"long": "RB Leipzig"}}},
    "players": {"TIMO_WERNER_1_BUNDESLIGA": {"name": "Timo Werner"}},
    "odds": {
        # main soccer markets live at periodID 'reg', 3-way ML
        "points-home-reg-ml3way-home": {
            "statID": "points", "statEntityID": "home", "periodID": "reg",
            "betTypeID": "ml3way", "sideID": "home",
            "byBookmaker": _bb(fanduel={"odds": "+150"},
                               draftkings={"odds": "+145"},
                               unknown={"odds": "+145"})},      # must be dropped
        "points-away-reg-ml3way-away": {
            "statID": "points", "statEntityID": "away", "periodID": "reg",
            "betTypeID": "ml3way", "sideID": "away",
            "byBookmaker": _bb(fanduel={"odds": "+200"})},
        "points-all-reg-ml3way-draw": {
            "statID": "points", "statEntityID": "all", "periodID": "reg",
            "betTypeID": "ml3way", "sideID": "draw",
            "byBookmaker": _bb(fanduel={"odds": "+240"})},
        # 2-way DNB moneyline must be skipped (not comparable to 3-way h2h)
        "points-home-reg-ml-home": {
            "statID": "points", "statEntityID": "home", "periodID": "reg",
            "betTypeID": "ml", "sideID": "home",
            "byBookmaker": _bb(fanduel={"odds": "+115"})},
        # yn player prop at periodID 'game' with explicit playerID
        "firstToScore-TIMO_WERNER_1_BUNDESLIGA-game-yn-yes": {
            "statID": "firstToScore", "statEntityID": "TIMO_WERNER_1_BUNDESLIGA",
            "playerID": "TIMO_WERNER_1_BUNDESLIGA",
            "periodID": "game", "betTypeID": "yn", "sideID": "yes",
            "byBookmaker": _bb(fanduel={"odds": "+750"},
                               draftkings={"odds": "+900"})},
        # cancelled odds must be skipped
        "points-all-reg-ou-over": {
            "statID": "points", "statEntityID": "all", "periodID": "reg",
            "betTypeID": "ou", "sideID": "over", "cancelled": True,
            "byBookmaker": _bb(fanduel={"odds": "-110", "overUnder": "2.5"})},
    },
}


def test_soccer_normalize():
    ev = normalize_event(SOCCER_EVENT, CFG.sgo_league_map)
    books = {b["key"]: {m["key"]: m["outcomes"] for m in b["markets"]}
             for b in ev["bookmakers"]}
    assert "unknown" not in books, "'unknown' aggregate book must be dropped"
    fd = books["fanduel"]
    names = {o["name"] for o in fd["h2h"]}
    assert names == {"1. FC Union Berlin", "RB Leipzig", "Draw"}, \
        f"soccer h2h should be the 3-way market: {names}"
    # DNB 2-way ml skipped -> exactly 3 h2h outcomes at FanDuel
    assert len(fd["h2h"]) == 3, "DNB ml leaked into h2h"
    assert "player_firstToScore_yn" in fd, "yn player prop missing"
    yn = fd["player_firstToScore_yn"][0]
    assert yn["description"] == "Timo Werner" and "point" not in yn
    assert "totals" not in fd, "cancelled odds leaked"


PINN_BOOK = {"key": "pinnacle", "markets": [
    {"key": "h2h", "outcomes": [
        {"name": "Kansas City Royals", "price": 1.91},
        {"name": "Minnesota Twins", "price": 1.91}]},
]}


def test_conversion():
    assert abs(american_to_decimal("+110") - 2.10) < 1e-9
    assert abs(american_to_decimal("-110") - 1.9090909) < 1e-6
    assert american_to_decimal("junk") is None


def test_normalize():
    ev = normalize_event(SGO_EVENT, CFG.sgo_league_map)
    assert ev["sport"] == "baseball_mlb"          # league mapped for grading
    assert ev["home_team"] == "Kansas City Royals"
    books = {b["key"]: {m["key"]: m["outcomes"] for m in b["markets"]}
             for b in ev["bookmakers"]}
    assert "pinnacle" not in books, "SGO pinnacle entries must be dropped"
    assert "betonlineag" in books, "betonline must remap to betonlineag"
    assert "player_battingBases" in books["fanduel"], "prop market missing"
    over = [o for o in books["fanduel"]["player_battingBases"]
            if o["name"] == "Over"][0]
    assert over["point"] == 1.5 and over["description"] == "Bobby Witt Jr."
    assert is_prop("player_battingBases")
    assert not any("1h" in m for m in books.get("fanduel", {})), "period leak"
    # event-page link fallback lands on books without betslip deeplinks
    dk_ml = [o for o in books["draftkings"]["h2h"]][0]
    assert dk_ml.get("deeplink") == "https://sportsbook.draftkings.com/event/123"
    bo_ml = [o for o in books["betonlineag"]["h2h"]][0]
    assert bo_ml.get("deeplink") == "https://sports.betonline.ag/game/456", \
        "fallback must use the RAW book id before remapping"
    assert "spreads" not in books.get("fanduel", {}), "unavailable odds leak"


def test_merge_and_scan():
    ev = normalize_event(SGO_EVENT, CFG.sgo_league_map)
    cache = {(norm_team("Kansas City Royals"), norm_team("Minnesota Twins")):
             (T0, PINN_BOOK)}
    merged = merge_pinnacle(ev, cache)
    assert any(b["key"] == "pinnacle" for b in merged["bookmakers"])

    edges = scan_event(merged, merged["sport"], CFG)
    by = {(e.book, e.market, e.selection): e for e in edges}

    # game line: DK swept vs Pinnacle(OddsAPI)+BetOnline(SGO) consensus
    gl = by.get(("draftkings", "h2h", "Kansas City Royals"))
    assert gl and "pinnacle" in gl.anchor, f"game-line edge wrong: {list(by)}"
    # FanDuel swept on game lines too
    assert ("fanduel", "h2h", "Kansas City Royals") in by
    # prop: DK Over swept vs FanDuel-anchored fair
    pr = by.get(("draftkings", "player_battingBases", "Bobby Witt Jr. Over 1.5"))
    assert pr and pr.anchor == "fanduel", f"prop edge wrong: {list(by)}"
    # FanDuel never alerted on its own prop
    assert not any(b == "fanduel" and is_prop(m) for b, m, _ in by)


def test_merge_time_tolerance():
    ev = normalize_event(SGO_EVENT, CFG.sgo_league_map)
    stale = {(norm_team("Kansas City Royals"), norm_team("Minnesota Twins")):
             (T0 + timedelta(hours=6), PINN_BOOK)}  # different game
    merged = merge_pinnacle(ev, stale)
    assert not any(b["key"] == "pinnacle" for b in merged["bookmakers"])


if __name__ == "__main__":
    test_conversion()
    test_normalize()
    test_soccer_normalize()
    test_merge_and_scan()
    test_merge_time_tolerance()
    print("test_sgo: all assertions passed")
