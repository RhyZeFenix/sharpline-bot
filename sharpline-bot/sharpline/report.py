"""Performance report: python -m sharpline.report [alerts.db] [--discord]

Splits game lines (Pinnacle-anchored) from player props (FanDuel-anchored)
so each anchor's health is visible on its own. CLV is the number that
matters first; W/L record needs 300+ bets before it means anything.
"""
import os
import sqlite3
import sys

GRADABLE = ("h2h", "totals", "spreads")


def _bucket(conn, where: str, params: tuple) -> dict:
    out = {}
    rows = conn.execute(
        f"SELECT clv_pct FROM tracked WHERE clv_pct IS NOT NULL AND {where}",
        params).fetchall()
    if rows:
        clvs = [r[0] for r in rows]
        out["clv_avg"] = sum(clvs) / len(clvs)
        out["clv_beat"] = sum(1 for c in clvs if c > 0)
        out["clv_n"] = len(clvs)
    rows = conn.execute(
        f"SELECT result, price FROM tracked WHERE result IS NOT NULL "
        f"AND result != 'void' AND {where}", params).fetchall()
    if rows:
        out["w"] = sum(1 for r, _ in rows if r == "win")
        out["l"] = sum(1 for r, _ in rows if r == "loss")
        out["p"] = sum(1 for r, _ in rows if r == "push")
        out["profit"] = sum(
            (pr - 1.0) if r == "win" else (-1.0 if r == "loss" else 0.0)
            for r, pr in rows)
    return out


def _bucket_lines(name: str, b: dict) -> list:
    lines = [f"— {name} —"]
    if "clv_n" in b:
        lines.append(
            f"CLV: avg {b['clv_avg']:+.2f}% | beat close "
            f"{b['clv_beat']}/{b['clv_n']} ({b['clv_beat']/b['clv_n']*100:.0f}%)")
    else:
        lines.append("CLV: no closing lines captured yet.")
    if "w" in b:
        settled = b["w"] + b["l"]
        rec = f"Record: {b['w']}-{b['l']}-{b['p']}"
        if settled:
            rec += f" ({b['w']/settled*100:.1f}%)"
        rec += (f" | Flat P/L: {b['profit']:+.2f}u | "
                f"ROI: {b['profit']/max(1, settled)*100:+.1f}%")
        lines.append(rec)
    else:
        lines.append("Record: nothing graded yet.")
    return lines


def summary_text(db: str = "alerts.db") -> str:
    conn = sqlite3.connect(db)
    n, = conn.execute("SELECT COUNT(*) FROM tracked").fetchone()
    if not n:
        return "No tracked bets yet."
    q = ",".join("?" * len(GRADABLE))
    lines = [f"=== SharpLine Report — {n} alerts tracked ===", ""]
    lines += _bucket_lines(
        "Game lines (Pinnacle anchor)",
        _bucket(conn, f"market IN ({q})", GRADABLE))
    lines.append("")
    lines += _bucket_lines(
        "Player props (FanDuel anchor)",
        _bucket(conn, f"market NOT IN ({q})", GRADABLE))
    pend, = conn.execute(
        "SELECT COUNT(*) FROM tracked WHERE result IS NULL AND "
        f"market NOT IN ({q}) AND commence < datetime('now')",
        GRADABLE).fetchone()
    if pend:
        lines.append(f"({pend} started props ungraded — "
                     "python -m sharpline.tracker pending props)")

    lines.append("")
    lines.append("By book:")
    for (book, cnt, avg_ev, avg_clv, w, l, p, units) in conn.execute(
        "SELECT book, COUNT(*), AVG(ev_open), AVG(clv_pct), "
        "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN result='push' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN result='win' THEN price-1 "
        "    WHEN result='loss' THEN -1 ELSE 0 END) "
        "FROM tracked GROUP BY book ORDER BY COUNT(*) DESC"):
        w, l, p, units = w or 0, l or 0, p or 0, units or 0.0
        settled = w + l
        clv_s = f"{avg_clv:+.2f}%" if avg_clv is not None else "n/a"
        roi_s = f"{units / settled * 100:+.1f}%" if settled else "  n/a"
        lines.append(f"  {book:<13} {cnt:>4} picks | {w}-{l}-{p} | "
                     f"{units:+.2f}u | ROI {roi_s} | "
                     f"avg EV {avg_ev:+.2f}% | CLV {clv_s}")
    return "\n".join(lines)


def run(db: str = "alerts.db", to_discord: bool = False):
    text = summary_text(db)
    print(text)
    if to_discord:
        from .alerter import DiscordAlerter
        url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if DiscordAlerter(url).send_text(f"```\n{text}\n```"):
            print("\n(posted to Discord)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    to_discord = "--discord" in args
    args = [a for a in args if a != "--discord"]
    run(args[0] if args else "alerts.db", to_discord)
