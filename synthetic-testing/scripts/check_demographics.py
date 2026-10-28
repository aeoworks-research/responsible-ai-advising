"""Flag demographic attributes of the owner that appear in a run's handoff or record.

Runs on every run (main and stress) with a cheap model. Writes data/demographic_checks.csv:
one row per finding, plus a "none" row for runs with no findings, so every checked run
appears.

Usage:
  python scripts/check_demographics.py [--force] [RUN_ID ...]
"""
from __future__ import annotations

import argparse
import csv
import json

from common import DATA_DIR, RUNS_DIR, call_model, check_budget, ledger_add, load_config, load_template

DEMO_CSV = DATA_DIR / "demographic_checks.csv"
CATEGORIES = ["age", "race_ethnicity", "gender_sexuality", "disability_health", "family_status", "religion",
              "immigration"]
COLUMNS = ["run_id", "prompt", "persona", "run_n", "stress_test", "category", "quote", "checker_model"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["findings"], "properties": {
    "findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["category", "quote"],
        "properties": {"category": {"type": "string", "enum": CATEGORIES}, "quote": {"type": "string"}}}}}}


def check(cfg: dict, run: dict) -> list[dict]:
    base = {"run_id": run["run_id"], "prompt": run["prompt"], "persona": run["persona"], "run_n": run["run_n"],
            "stress_test": "yes" if run.get("stress_test") else "no",
            "checker_model": cfg["roles"]["demographic_checker"]["model"]}
    handoff = run.get("handoff_text") or ""
    if not handoff.strip():
        return [{**base, "category": "no_handoff", "quote": ""}]
    _, text, usage = call_model(cfg, "demographic_checker", load_template("demographic_checker.txt"),
                                [{"role": "user", "content": f"<document>\n{handoff}\n</document>"}],
                                output_format={"type": "json_schema", "schema": SCHEMA})
    ledger_add("check_demographics", run["run_id"], usage["cost_usd"])
    found = json.loads(text)["findings"]
    print(f"done  {run['run_id']}: {len(found)} finding(s)")
    return [{**base, **f} for f in found] or [{**base, "category": "none", "quote": ""}]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    existing = list(csv.DictReader(open(DEMO_CSV, newline="", encoding="utf-8"))) if DEMO_CSV.exists() else []
    done = {r["run_id"] for r in existing}
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RUNS_DIR.glob("*.json"))]
    if a.run_ids:
        runs = [r for r in runs if r["run_id"] in a.run_ids]
    if not a.force:
        runs = [r for r in runs if r["run_id"] not in done]
    new = []
    for run in runs:
        check_budget(cfg)
        try:
            new += check(cfg, run)
        except Exception as e:
            print(f"  {run['run_id']}: demographic check failed: {e}")
    redone = {r["run_id"] for r in new}
    DATA_DIR.mkdir(exist_ok=True)
    with open(DEMO_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows([r for r in existing if r["run_id"] not in redone] + new)
    print(f"wrote {DEMO_CSV.name}")


if __name__ == "__main__":
    main()
