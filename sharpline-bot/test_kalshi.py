"""Offline test of Kalshi matching + reciprocal depth math with a mocked book."""
from sharpline.kalshi_depth import KalshiDepth, _tokens

kd = KalshiDepth()
# fake the cache so no network is needed
kd._markets = [
    {"ticker": "KXMLBGAME-26JUL03NYYBOS-NYY",
     "title": "Yankees vs Red Sox Winner?",
     "yes_sub_title": "Yankees",
     "subtitle": ""},
    {"ticker": "KXMLBTOTAL-26JUL03NYYBOS-T8.5",
     "title": "Yankees vs Red Sox: total runs 8.5 or more?",
     "yes_sub_title": "8.5 or more",
     "subtitle": ""},
    {"ticker": "KXNBA-UNRELATED", "title": "Lakers vs Celtics Winner?",
     "yes_sub_title": "Lakers", "subtitle": ""},
]
import time; kd._fetched_at = time.time()

m = kd.match("Yankees @ Red Sox", "Over 8.5")
print("matched:", m["ticker"])
assert "TOTAL" in m["ticker"]

m2 = kd.match("Yankees @ Red Sox", "Yankees")
print("matched:", m2["ticker"])
assert m2["ticker"].endswith("NYY")

# reciprocal depth: buying YES at <=48c fills vs NO bids >= 52c
book = {"yes": [[40, 500], [45, 200]],
        "no":  [[50, 100], [52, 300], [55, 150], [60, 80]]}
contracts, dollars, best = KalshiDepth.fillable(book, "yes", 48)
print(f"fillable: {contracts} contracts ${dollars:.2f}, best ask {best}c")
# no@52 -> ask 48 (300), no@55 -> ask 45 (150), no@60 -> ask 40 (80); no@50 -> ask 50 too pricey
assert contracts == 530 and best == 40
assert abs(dollars - (300*.48 + 150*.45 + 80*.40)) < 1e-9

# side inference: "Over 8.5" tokens overlap yes_sub_title "8.5 or more" -> YES
side_text = f"{m.get('yes_sub_title','')} {m.get('title','')}"
assert _tokens("Over 8.5") & _tokens(side_text)
print("kalshi depth module ok")
