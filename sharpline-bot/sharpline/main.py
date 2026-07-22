"""SharpLine EV Bot — main loop."""

import logging
import os
import time
from datetime import datetime, timezone

from .config import Config
from .odds_client import OddsClient
from .scanner import scan_event, consensus_labels
from .alerter import AlertStore, DiscordAlerter
from .kalshi_depth import KalshiDepth
from .tracker import Tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("sharpline")


def build_config() -> Config:
    return Config(
        odds_api_key=os.environ.get("ODDS_API_KEY", ""),
        sgo_api_key=os.environ.get("SGO_API_KEY", ""),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        discord_results_webhook_url=os.environ.get(
            "DISCORD_RESULTS_WEBHOOK_URL", ""),
        book_webhooks_json=os.environ.get("BOOK_WEBHOOKS_JSON", ""),
        supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
        discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
        scan_props=os.environ.get("SCAN_PROPS", "0") == "1",
    )


def run():
    cfg = build_config()
    if not cfg.odds_api_key:
        raise SystemExit("Set ODDS_API_KEY env var.")
    if cfg.sgo_api_key:
        run_dual(cfg)      # SGO soft books/props + Odds API Pinnacle anchor
    else:
        run_legacy(cfg)    # original single-source Odds API mode


def run_dual(cfg: Config):
    """Two independent cadences:
      - SGO sweep (default 15 min): soft prices + props, one object/event
      - Pinnacle refresh (default 30 min): anchor game lines, 3 credits/sport
    Non-overlap: Pinnacle enters ONLY via OddsClient.pinnacle_odds (game
    lines only); SGO's adapter drops any pinnacle entries and is the ONLY
    source of props and soft prices."""
    import json as _json
    import re as _re
    from .sgo_client import (SGOClient, normalize_event, merge_pinnacle,
                             norm_team, prop_scores)
    from .supabase_writer import SupabaseWriter

    client = OddsClient(cfg.odds_api_key, cfg.request_timeout)
    sgo = SGOClient(cfg.sgo_api_key, cfg.request_timeout)
    store = AlertStore(cfg.db_path)
    alerter = DiscordAlerter(cfg.discord_webhook_url)
    kalshi = KalshiDepth(cfg.request_timeout)
    tracker = Tracker(cfg.db_path)
    supa = SupabaseWriter(cfg.supabase_url, cfg.supabase_service_key)
    if supa.enabled:
        log.info("Supabase mirror enabled.")
    # results/reports get their own channel when configured
    results_alerter = DiscordAlerter(
        cfg.discord_results_webhook_url or cfg.discord_webhook_url)
    # optional per-book channels: BOOK_WEBHOOKS_JSON = {"draftkings": "https://...", ...}
    book_alerters = {}
    if cfg.book_webhooks_json:
        try:
            book_alerters = {b: DiscordAlerter(u) for b, u in
                             _json.loads(cfg.book_webhooks_json).items()}
            log.info("Per-book Discord routing for: %s",
                     ", ".join(book_alerters))
        except (ValueError, AttributeError) as e:
            log.warning("BOOK_WEBHOOKS_JSON invalid, ignoring: %s", e)
    _PROP_SEL = _re.compile(r"^(.+?) (Over|Under) ([0-9.]+)$")

    def enrich(edge):
        if edge.book == "kalshi":
            try:
                edge.depth = kalshi.depth_note(
                    edge.event, edge.selection, edge.odds)
            except Exception as e:
                log.warning("Kalshi depth failed: %s", e)
        return edge

    # (home_norm, away_norm) -> (commence_dt, pinnacle bookmaker dict)
    pinnacle_cache: dict = {}
    sports = [cfg.sgo_league_map[lg] for lg in cfg.sgo_leagues
              if lg in cfg.sgo_league_map]
    last_pinn = last_sgo = 0.0
    last_grade_sync = time.time()   # only announce grades from this boot on
    last_report_date = None
    pinn_s = cfg.sweep_interval_pinnacle_min * 60
    sgo_s = cfg.sweep_interval_sgo_min * 60

    while True:
        try:
            now = time.time()

            # ---- Pinnacle anchor refresh (every 30 min) ----
            if now - last_pinn >= pinn_s:
                rem = client.credits_remaining
                if rem is not None and float(rem) < cfg.credits_floor:
                    log.warning("Odds API credits at %s (< floor %d); "
                                "skipping anchor refresh.", rem, cfg.credits_floor)
                else:
                    fresh = 0
                    for sport in sports:
                        # FREE pre-check: skip paid call if no games in window
                        upcoming = client.events(sport)
                        if not _any_in_window(upcoming, cfg):
                            continue
                        for ev in client.pinnacle_odds(
                                sport, cfg.markets, cfg.odds_format,
                                cfg.odds_api_bookmakers):
                            books = ev.get("bookmakers") or []
                            if not books or not books[0].get("markets"):
                                continue
                            try:
                                t = datetime.fromisoformat(
                                    ev["commence_time"].replace("Z", "+00:00"))
                            except (KeyError, ValueError):
                                continue
                            key = (norm_team(ev.get("home_team")),
                                   norm_team(ev.get("away_team")))
                            pinnacle_cache[key] = (t, books[0])
                            fresh += 1
                    # grade finished game lines while we're here (2 cr/sport)
                    for gs in tracker.sports_pending():
                        tracker.grade_sport(gs, client.scores(gs))
                    # ---- auto-grade props via SGO finalized box scores ----
                    pend = [(k, c, mk, sel) for k, c, mk, sel
                            in tracker.pending_prop_rows()
                            if not mk.endswith("_yn")]
                    started = []
                    now_dt = datetime.now(timezone.utc)
                    for k, c, mk, sel in pend:
                        try:
                            t0 = datetime.fromisoformat(
                                c.replace("Z", "+00:00"))
                            if (now_dt - t0).total_seconds() > 2.5 * 3600:
                                started.append((k, mk, sel))
                        except (ValueError, AttributeError):
                            continue
                    if started:
                        try:
                            scores = {}
                            for fev in sgo.events(list(cfg.sgo_leagues),
                                                  finalized=True):
                                scores.update(prop_scores(fev))
                            n_props = 0
                            for k, mk, sel in started:
                                mo = _PROP_SEL.match(sel)
                                if not mo:
                                    continue
                                sc = scores.get(
                                    (k.split("|")[0], mk, mo.group(1)))
                                if sc is None:
                                    continue
                                point = float(mo.group(3))
                                if sc == point:
                                    result = "push"
                                elif (sc > point) == (mo.group(2) == "Over"):
                                    result = "win"
                                else:
                                    result = "loss"
                                if tracker.set_result(k, result):
                                    n_props += 1
                            if n_props:
                                log.info("Auto-graded %d props via SGO "
                                         "box scores.", n_props)
                        except Exception:
                            log.exception("SGO prop grading failed; "
                                          "will retry next cycle.")

                    # ---- sync fresh grades -> Supabase + Discord settle notice ----
                    newly = tracker.graded_since(last_grade_sync)
                    if newly:
                        emoji = {"win": "✅", "loss": "❌",
                                 "push": "➖", "void": "⚪"}
                        lines = []
                        for (key, sel, book, price, result,
                             clv, gts) in newly:
                            supa.update_result(key, result, clv)
                            units = ((price - 1.0) if result == "win"
                                     else (-1.0 if result == "loss" else 0.0))
                            line = (f"{emoji.get(result, '•')} {sel} @ {book} "
                                    f"— {key.split('|')[0]}: {result.upper()} "
                                    f"({units:+.2f}u)")
                            if clv is not None:
                                line += f", CLV {clv:+.1f}%"
                            lines.append(line)
                            last_grade_sync = max(last_grade_sync, gts)
                        results_alerter.send_text(
                            "**Settled picks**\n" + "\n".join(lines[:25]))
                        log.info("Synced %d grades to Supabase/Discord.",
                                 len(newly))
                    log.info("Anchor refresh: %d Pinnacle events cached. "
                             "Credits left: %s", fresh, client.credits_remaining)
                last_pinn = now

            # ---- SGO sweep (every 15 min) ----
            if now - last_sgo >= sgo_s:
                raw = sgo.events(list(cfg.sgo_leagues), cfg.max_hours_to_start)
                n_edges = n_alerts = n_merged = 0
                for raw_ev in raw:
                    ev = normalize_event(raw_ev, cfg.sgo_league_map)
                    if not ev:
                        continue
                    ev = merge_pinnacle(ev, pinnacle_cache)
                    if any(b.get("key") == cfg.sharp_book
                           for b in ev["bookmakers"]):
                        n_merged += 1
                    sport = ev.get("sport") or "unknown"
                    for edge in scan_event(ev, sport, cfg):
                        n_edges += 1
                        if store.should_alert(edge, cfg.realert_ev_improvement):
                            dest = book_alerters.get(edge.book, alerter)
                            if dest.send(enrich(edge)):
                                store.record(edge)
                                tracker.record(edge)
                                supa.insert_alert(edge)
                                n_alerts += 1
                    lbl = f"{ev.get('away_team','?')} @ {ev.get('home_team','?')}"
                    tracker.update_closes(lbl, consensus_labels(ev, cfg))
                log.info("SGO sweep: %d events (%d with Pinnacle anchor), "
                         "%d edges, %d alerts. Objects this month: %s",
                         len(raw), n_merged, n_edges, n_alerts,
                         sgo.usage() or "n/a")
                last_sgo = now

            # ---- daily results report to Discord ----
            now_utc = datetime.now(timezone.utc)
            if (cfg.daily_report_hour_utc >= 0
                    and now_utc.hour >= cfg.daily_report_hour_utc
                    and last_report_date != now_utc.date()):
                from .report import summary_text
                if results_alerter.send_text(
                        f"```\n{summary_text(cfg.db_path)}\n```"):
                    last_report_date = now_utc.date()
                    log.info("Posted daily report to Discord.")

            next_due = min(last_pinn + pinn_s, last_sgo + sgo_s)
            time.sleep(max(30.0, next_due - time.time()))

        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception:
            log.exception("Cycle failed; sleeping and retrying.")
            time.sleep(cfg.poll_seconds)


def _any_in_window(upcoming: list, cfg: Config) -> bool:
    now = datetime.now(timezone.utc)
    for e in upcoming:
        try:
            t = datetime.fromisoformat(
                e["commence_time"].replace("Z", "+00:00"))
            h = (t - now).total_seconds() / 3600.0
            if cfg.min_hours_to_start <= h <= cfg.max_hours_to_start:
                return True
        except (KeyError, ValueError):
            return True
    return False


def run_legacy(cfg: Config):

    client = OddsClient(cfg.odds_api_key, cfg.request_timeout)
    store = AlertStore(cfg.db_path)
    alerter = DiscordAlerter(cfg.discord_webhook_url)
    kalshi = KalshiDepth(cfg.request_timeout)
    tracker = Tracker(cfg.db_path)

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
    last_report_date = None

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
                            dest = book_alerters.get(edge.book, alerter)
                            if dest.send(enrich(edge)):
                                store.record(edge)
                                tracker.record(edge)
                                n_alerts += 1
                    # refresh closing-line snapshot for tracked bets (free)
                    lbl = f"{ev.get('away_team','?')} @ {ev.get('home_team','?')}"
                    tracker.update_closes(lbl, consensus_labels(ev, cfg))

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
                                dest = book_alerters.get(edge.book, alerter)
                            if dest.send(enrich(edge)):
                                    store.record(edge)
                                    tracker.record(edge)
                                    n_alerts += 1
                        lbl = (f"{detail.get('away_team','?')} @ "
                               f"{detail.get('home_team','?')}")
                        tracker.update_closes(lbl, consensus_labels(detail, cfg))

            # ---- grade finished games (2 credits/sport, max 2x/hour) ----
            for gs in tracker.sports_pending():
                tracker.grade_sport(gs, client.scores(gs))

            # ---- daily results report to Discord ----
            now_utc = datetime.now(timezone.utc)
            if (cfg.daily_report_hour_utc >= 0
                    and now_utc.hour >= cfg.daily_report_hour_utc
                    and last_report_date != now_utc.date()):
                from .report import summary_text
                if alerter.send_text(f"```\n{summary_text(cfg.db_path)}\n```"):
                    last_report_date = now_utc.date()
                    log.info("Posted daily report to Discord.")

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
