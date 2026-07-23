"""Discord webhook pings + sqlite dedup so the same edge doesn't spam."""

import logging
import sqlite3
import time

import requests

from .scanner import Edge

log = logging.getLogger("sharpline.alerter")


def american(dec: float) -> str:
    if dec >= 2.0:
        return f"+{round((dec - 1) * 100)}"
    return f"-{round(100 / (dec - 1))}"


class AlertStore:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS alerts ("
            "key TEXT PRIMARY KEY, best_ev REAL, last_ts REAL)"
        )
        self.conn.commit()

    def should_alert(self, edge: Edge, improvement_pts: float) -> bool:
        row = self.conn.execute(
            "SELECT best_ev FROM alerts WHERE key = ?", (edge.key,)
        ).fetchone()
        if row is None:
            return True
        return edge.ev >= row[0] + improvement_pts

    def record(self, edge: Edge):
        self.conn.execute(
            "INSERT INTO alerts(key, best_ev, last_ts) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET best_ev=excluded.best_ev, "
            "last_ts=excluded.last_ts",
            (edge.key, edge.ev, time.time()),
        )
        self.conn.commit()


class DiscordAlerter:
    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send_text(self, content: str) -> bool:
        """Plain-text post (used by the daily tracker report)."""
        if not self.url:
            log.info("No webhook set — printing:\n%s", content)
            return True
        try:
            r = requests.post(self.url, json={"content": content[:2000]},
                              timeout=10)
            if r.status_code == 429:
                time.sleep(2)
                r = requests.post(self.url, json={"content": content[:2000]},
                                  timeout=10)
            return r.status_code in (200, 204)
        except requests.RequestException as e:
            log.error("Discord send failed: %s", e)
            return False

    def send(self, edge: Edge) -> bool:
        if not self.url:
            log.info("No webhook set — printing edge:\n%s", self._text(edge))
            return True
        # DFS pick'em entries aren't priced bets: odds holds the entry
        # payout multiplier and EV is for the full entry, so the embed
        # frames it as an entry leg rather than a bookable price.
        is_dfs = "-pick" in (edge.depth or "")
        if is_dfs:
            title = f"🧩 +{edge.ev:.2f}% entry EV — {edge.selection}"
            price_field = {"name": "Entry pays",
                           "value": f"{edge.odds:g}x (both legs must hit)",
                           "inline": True}
            stake_name = "Entry stake (¼ Kelly)"
        else:
            title = f"🎯 +{edge.ev:.2f}% EV — {edge.selection}"
            price_field = {"name": "Price",
                           "value": f"{edge.odds:.3f} ({american(edge.odds)})",
                           "inline": True}
            stake_name = "Stake (¼ Kelly)"
        embed = {
            "title": title,
            "description": edge.event,
            "color": (0x9B59B6 if is_dfs else
                      0x2ECC71 if edge.ev >= 4 else 0xF1C40F),
            "fields": [
                {"name": "Book", "value": edge.book, "inline": True},
                price_field,
                {"name": "Fair", "value": f"{edge.fair_odds:.3f} ({american(edge.fair_odds)})", "inline": True},
                {"name": "Fair Win%", "value": f"{edge.fair_prob*100:.1f}%", "inline": True},
                {"name": stake_name, "value": f"{edge.stake_units:.2f}u", "inline": True},
                {"name": "Market", "value": f"{edge.market} · {edge.sport}", "inline": True},
            ],
            "footer": {"text": f"anchor: {edge.anchor} · starts {edge.commence}"},
        }
        if edge.depth:
            embed["fields"].append(
                {"name": "Entry" if is_dfs else "Liquidity",
                 "value": edge.depth[:1000], "inline": False})
        try:
            r = requests.post(self.url, json={"embeds": [embed]}, timeout=10)
            if r.status_code == 429:
                time.sleep(2)
                r = requests.post(self.url, json={"embeds": [embed]}, timeout=10)
            return r.status_code in (200, 204)
        except requests.RequestException as e:
            log.error("Discord send failed: %s", e)
            return False

    @staticmethod
    def _text(edge: Edge) -> str:
        return (f"+{edge.ev:.2f}% EV | {edge.selection} @ {edge.book} "
                f"{edge.odds:.3f} (fair {edge.fair_odds:.3f}) | "
                f"{edge.event} | stake {edge.stake_units:.2f}u")
