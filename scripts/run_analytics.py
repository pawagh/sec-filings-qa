"""Run every query in sql/analytics, report rows and latency (acceptance: each < 200 ms).

    python scripts/run_analytics.py            # summary table
    python scripts/run_analytics.py --show 5   # also print the first 5 rows of each
"""

import argparse
import sys
import time
from pathlib import Path

from secqa.config import ROOT
from secqa.db import connect


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--show", type=int, default=0)
    p.add_argument("--budget-ms", type=float, default=200)
    args = p.parse_args()
    slow = 0
    with connect() as conn:
        for path in sorted(Path(ROOT / "sql" / "analytics").glob("*.sql")):
            t0 = time.perf_counter()
            cur = conn.execute(path.read_text())
            rows = cur.fetchall()
            ms = (time.perf_counter() - t0) * 1000
            slow += ms > args.budget_ms
            print(f"{path.name:<45} {len(rows):>5} rows {ms:>8.1f} ms{'  SLOW' if ms > args.budget_ms else ''}")
            if args.show:
                cols = [d.name for d in cur.description]
                print("    " + " | ".join(cols))
                for r in rows[: args.show]:
                    print("    " + " | ".join(str(v) for v in r))
    return 1 if slow else 0


if __name__ == "__main__":
    sys.exit(main())
