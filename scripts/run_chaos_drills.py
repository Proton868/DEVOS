#!/usr/bin/env python3
"""Run Production Readiness Gate v1 pure-logic chaos drills and write a report.

  python scripts/run_chaos_drills.py
  python scripts/run_chaos_drills.py --out data/chaos/report.json

Exit 1 if any drill fails.
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/chaos/latest_report.json")
    args = ap.parse_args()
    from chaos.drills import run_all_drills
    report = await run_all_drills()
    out = Path(args.out)
    report.write(out)
    print(f"Chaos drills: {report.passed}/{len(report.results)} passed — gate={report.to_dict()['gate']}")
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"  [{mark}] {r.name}: {r.actual}")
    print(f"Report: {out}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
