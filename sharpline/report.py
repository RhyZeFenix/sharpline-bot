"""Performance report: python -m sharpline.report [alerts.db]"""
import sqlite3
import sys


def run(db="alerts.db"):
    conn = sqlite3.connect(db)
    n, = conn.execute("SELECT COUNT(*) FROM tracked").fetchone()
    if not n:
        print("No tracked bets yet.")
        return
    print(f"=== SharpLine Report — {n} alerts tracked ===\n")

    # CLV (system health — the number that matters first)
    rows = conn.execute(
        "SELECT clv_pct FROM tracked WHERE clv_pct IS NOT NULL").fetchall()
    if rows:
        clvs = [r[0] for r in rows]
        beat = sum(1 for c in clvs if c > 0)
        print(f"CLV: avg {sum(clvs)/len(clvs):+.2f}% | "
              f"beat close {beat}/{len(clvs)} ({beat/len(clvs)*100:.0f}%)")
    else:
        print("CLV: no closing lines captured yet.")

    # W/L (flat 1u staking)
    rows = conn.execute(
        "SELECT result, price FROM tracked WHERE result IS NOT NULL").fetchall()
    if rows:
        w = sum(1 for r, _ in rows if r == "win")
        l = sum(1 for r, _ in rows if r == "loss")
        p = sum(1 for r, _ in rows if r == "push")
        profit = sum((pr - 1.0) if r == "win" else (-1.0 if r == "loss" else 0.0)
                     for r, pr in rows)
        settled = w + l
        print(f"Record: {w}-{l}-{p}"
              + (f" ({w/settled*100:.1f}%)" if settled else "")
              + f" | Flat P/L: {profit:+.2f}u | "
                f"ROI: {profit/max(1,settled)*100:+.1f}%")
    else:
        print("Record: nothing graded yet (props are never auto-graded).")

    # breakdown by book
    print("\nBy book:")
    for book, cnt, avg_ev, avg_clv in conn.execute(
        "SELECT book, COUNT(*), AVG(ev_open), AVG(clv_pct) "
        "FROM tracked GROUP BY book ORDER BY COUNT(*) DESC"):
        clv_s = f"{avg_clv:+.2f}%" if avg_clv is not None else "n/a"
        print(f"  {book:<15} {cnt:>4} alerts | avg EV {avg_ev:+.2f}% | avg CLV {clv_s}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "alerts.db")
