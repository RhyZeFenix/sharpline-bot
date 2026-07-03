"""SharpLine EV Bot — main loop."""

import logging
import os
import time
from datetime import datetime, timezone

from .config import Config
from .odds_client import OddsClient
from .scanner import scan_event
from .alerter import AlertStore, DiscordAlerter
from .kalshi_depth import KalshiDepth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("sharpline")


def build_config() -> Config:
    return Config(
        odds_api_key=os.environ.get("ODDS_API_KEY", ""),
        discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
        scan_props=os.environ.get("SCAN_PROPS", "0") == "1",
    )


def run():
    cfg = build_config()
    if not cfg.odds_api_key:
        raise SystemExit("Set ODDS_API_KEY env var.")

    client = OddsClient(cfg.odds_api_key, cfg.request_timeout)
    store = AlertStore(cfg.db_path)
    alerter = DiscordAlerter(cfg.discord_webhook_url)
    kalshi = KalshiDepth(cfg.request_timeout)

    def enrich(edge):
        if edge.book == "kalshi":
            try:
                edge.depth = kalshi.depth_note(
                    edge.event, edge.selection, edge.odds)
            except Exception as e:
                log.warning("Kalshi depth failed: %s", e)
        return edge

    sports: list = []
    cycle = 0

    while True:
        try:
            if cycle % cfg.sports_refresh_cycles == 0:
                active = client.active_sports(cfg.exclude_sports)
                sports = ([s for s in active if s in cfg.include_sports]
                          if cfg.include_sports else active)
                log.info("Tracking %d sports: %s", len(sports), ", ".join(sports))

            # hard stop if quota nearly exhausted
            rem = client.credits_remaining
            if rem is not None and float(rem) < cfg.credits_floor:
                log.warning("Credits at %s (< floor %d). Sleeping 1h.",
                            rem, cfg.credits_floor)
                time.sleep(3600)
                continue

            used_before = float(client.credits_used or 0)

            n_edges = n_alerts = 0
            for sport in sports:
                # FREE pre-check: skip the paid odds call if no games
                # start inside our betting window.
                upcoming = client.events(sport)
                now = datetime.now(timezone.utc)
                def _in_window(e):
                    try:
                        t = datetime.fromisoformat(
                            e["commence_time"].replace("Z", "+00:00"))
                        h = (t - now).total_seconds() / 3600.0
                        return cfg.min_hours_to_start <= h <= cfg.max_hours_to_start
                    except (KeyError, ValueError):
                        return True
                if not any(_in_window(e) for e in upcoming):
                    continue

                events = client.odds(
                    sport, cfg.regions, cfg.markets, cfg.odds_format)
                for ev in events:
                    for edge in scan_event(ev, sport, cfg):
                        n_edges += 1
                        if store.should_alert(edge, cfg.realert_ev_improvement):
                            if alerter.send(enrich(edge)):
                                store.record(edge)
                                n_alerts += 1

                # ---- optional player props (credit-heavy) ----
                if cfg.scan_props and events:
                    for ev in events[: cfg.max_prop_events_per_cycle]:
                        detail = client.event_odds(
                            sport, ev["id"], cfg.regions,
                            cfg.prop_markets, cfg.odds_format)
                        if not detail:
                            continue
                        for edge in scan_event(detail, sport, cfg, is_props=True):
                            n_edges += 1
                            if store.should_alert(edge, cfg.realert_ev_improvement):
                                if alerter.send(enrich(edge)):
                                    store.record(edge)
                                    n_alerts += 1

            # ---- budget pacing: stretch sleep so a full day of cycles
            # never exceeds daily_credit_budget ----
            cycle_cost = max(0.0, float(client.credits_used or 0) - used_before)
            budget_sleep = (cycle_cost * 86400.0 / cfg.daily_credit_budget
                            if cycle_cost > 0 else cfg.poll_seconds)
            sleep_s = max(cfg.poll_seconds, budget_sleep)
            log.info(
                "Cycle %d: %d edges, %d alerts. Cost %.0f credits, left %s. "
                "Next scan in %.0f min.",
                cycle, n_edges, n_alerts, cycle_cost,
                client.credits_remaining, sleep_s / 60.0,
            )
            cycle += 1
            time.sleep(sleep_s)

        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception:
            log.exception("Cycle failed; sleeping and retrying.")
            time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    run()
