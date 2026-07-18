from datetime import datetime, timedelta, timezone
from sharpline.devig import devig, ev_pct, kelly_units
from sharpline.scanner import scan_event
from sharpline.config import Config

fair = devig([1.952, 1.952], "power"); assert abs(fair[0]-0.5) < 1e-6
fair = devig([1.40, 3.10], "power"); assert abs(sum(fair)-1.0) < 1e-9
assert ev_pct(0.52, 2.05) > 6
assert kelly_units(0.52, 2.05, 0.25, 100) > 1
print("math ok")

soon = (datetime.now(timezone.utc)+timedelta(hours=5)).isoformat().replace("+00:00","Z")
far  = (datetime.now(timezone.utc)+timedelta(hours=90)).isoformat().replace("+00:00","Z")

def mk_event(start):
    return {
      "away_team":"Yankees","home_team":"Red Sox","commence_time":start,
      "bookmakers":[
        {"key":"pinnacle","markets":[{"key":"totals","outcomes":[
            {"name":"Over","point":8.5,"price":1.92},
            {"name":"Under","point":8.5,"price":1.92}]}]},
        {"key":"betonlineag","markets":[{"key":"totals","outcomes":[
            {"name":"Over","point":8.5,"price":1.90},
            {"name":"Under","point":8.5,"price":1.94}]}]},
        {"key":"draftkings","markets":[{"key":"totals","outcomes":[
            {"name":"Over","point":8.5,"price":2.10},
            {"name":"Under","point":8.5,"price":1.75}]}]},
        {"key":"fanduel","markets":[{"key":"totals","outcomes":[
            {"name":"Over","point":9.0,"price":2.10}]}]},
      ]}

cfg = Config(odds_api_key="x")
edges = scan_event(mk_event(soon), "baseball_mlb", cfg)
for ed in edges:
    print(f"EDGE: {ed.selection} @ {ed.book} {ed.odds} -> +{ed.ev:.2f}% EV "
          f"(fair {ed.fair_prob*100:.1f}%, anchor {ed.anchor}, stake {ed.stake_units:.2f}u)")
assert len(edges)==1 and edges[0].book=="draftkings" and "pinnacle" in edges[0].anchor
assert scan_event(mk_event(far), "baseball_mlb", cfg) == []   # 90h out -> skipped
print("consensus + point-match + time-filter ok")
