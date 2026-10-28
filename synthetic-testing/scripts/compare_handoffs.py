"""Blind side-by-side comparison of advisor handoffs for the same persona.

The judge sees the persona fact sheet and two handoffs (converted from HTML to Markdown),
labeled only "first" and "second". Each pair is judged in both orders to cancel position
bias. Rows go to scores/pairwise.csv; full judgments to scores/pairwise.jsonl.

Pairs:
  default        cross-prompt pairs with the same run number (the main comparison)
  --all-pairs    every pair of runs within a persona (used to rank runs for the human check)
  --human-pairs  pairs a human reviewer has judged: stated pairs (human_pairwise.csv) plus same-persona pairs
                 with different handoff scores in a scoring sheet

Judgments are keyed by the judge template's hash, so a revised template re-judges pairs
instead of mixing with earlier judgments (v1 template: templates/pairwise_judge_v1_2026-10-24.txt).

Usage:
  python scripts/compare_handoffs.py [--all-pairs | --human-pairs] [--persona P ...] [--one-order]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import json

import pypandoc

from common import (HUMAN_DIR, RUNS_DIR, SCORES_DIR, call_model, check_budget, fact_sheet_text, ledger_add,
                    load_config, load_persona, load_template, sha256_text)

PAIR_CSV = SCORES_DIR / "pairwise.csv"
PAIR_JSONL = SCORES_DIR / "pairwise.jsonl"
# Criteria for the current judge template (v1). The 2026-10-27 experiment
# (templates/pairwise_judge_v2_2026-10-27_experiment.txt) used followup_questions, ai_opportunities,
# context_behind_toplines, priorities_and_evidence, accuracy; it agreed with a human reviewer no better than v1.
CRITERIA = ["context_behind_toplines", "accuracy", "specificity", "priorities_and_evidence",
            "followup_questions", "skimmability", "guardrails"]
COLUMNS = ["comparison_id", "persona", "run_first", "run_second", "prompt_first", "prompt_second",
           "run_n_first", "run_n_second", "winner", "winner_run", "strength", "judge_model", "judge_effort",
           "template_sha256", "timestamp", "cost_usd", "reason"]


def schema() -> dict:
    def obj(p):
        return {"type": "object", "properties": p, "required": list(p), "additionalProperties": False}
    side = {"type": "string", "enum": ["first", "second", "tie"]}
    return obj({
        "criteria": {"type": "array", "items": obj({"criterion": {"type": "string", "enum": CRITERIA},
                                                     "better": side, "note": {"type": "string"}})},
        "overall_better": side,
        "strength": {"type": "string", "enum": ["slight", "clear", "strong"]},
        "reason": {"type": "string"},
    })


MD_FORMAT = ("markdown-raw_html-native_divs-native_spans-fenced_divs-bracketed_spans"
             "-link_attributes-header_attributes")
_md_cache: dict[str, str] = {}


def handoff_md(run: dict) -> str:
    if run["run_id"] not in _md_cache:
        html = RUNS_DIR / f"{run['run_id']}.handoff.html"
        if html.exists():
            # Markdown without raw HTML/attributes: keeps headings, lists and tables, drops styling.
            # Row colors (record status) are lost; status words and badges survive as text.
            text = pypandoc.convert_file(str(html), MD_FORMAT, extra_args=["--wrap=none"])
        else:
            text = run["handoff_text"]
        _md_cache[run["run_id"]] = text
    return _md_cache[run["run_id"]]


def judge(cfg: dict, first: dict, second: dict) -> tuple[dict, float]:
    persona = load_persona(first["persona"])
    msg = (f"OWNER FACT SHEET ({persona['label']})\n{fact_sheet_text(persona)}\n\n"
           f"FIRST HANDOFF\n<handoff>\n{handoff_md(first)}\n</handoff>\n\n"
           f"SECOND HANDOFF\n<handoff>\n{handoff_md(second)}\n</handoff>")
    _, text, usage = call_model(cfg, "pairwise_judge", load_template("pairwise_judge.txt"),
                                [{"role": "user", "content": msg}],
                                output_format={"type": "json_schema", "schema": schema()})
    return json.loads(text), usage["cost_usd"]


def pairs_for(runs: list[dict], all_pairs: bool) -> list[tuple[dict, dict]]:
    by_persona: dict[tuple[str, str], list[dict]] = {}  # never pair runs from different owner versions
    for r in runs:
        by_persona.setdefault((r["persona"], r.get("owner_sim_version", "v1")), []).append(r)
    out = []
    for lst in by_persona.values():
        lst.sort(key=lambda r: r["run_id"])
        for a, b in itertools.combinations(lst, 2):
            if all_pairs or (a["prompt"] != b["prompt"] and a["run_n"] == b["run_n"]):
                out.append((a, b))
    return out


def human_pairs() -> list[tuple[str, str]]:
    """(better, worse) run IDs from a human reviewer's judgments: stated pairs, plus same-persona pairs whose
    handoff scores differ in a scoring sheet."""
    import pandas as pd
    out = []
    for key_path in sorted(HUMAN_DIR.glob("batch_*/_key.csv")):
        key = {r["sample_id"]: r["run_id"] for r in csv.DictReader(open(key_path, encoding="utf-8"))}
        stated = key_path.parent / "human_pairwise.csv"
        if stated.exists():
            out += [(key[r["better"]], key[r["worse"]]) for r in csv.DictReader(open(stated, encoding="utf-8"))]
        sheet = key_path.parent / "scoring_sheet.xlsx"
        if sheet.exists():
            h = pd.read_excel(sheet, sheet_name="scores")
            h = h[pd.to_numeric(h.get("handoff_score"), errors="coerce").notna()] if "handoff_score" in h else h[:0]
            rows = [(key[s], float(v)) for s, v in zip(h.sample_id, h.handoff_score)]
            persona = {rid: json.loads((RUNS_DIR / f"{rid}.json").read_text(encoding="utf-8"))["persona"]
                       for rid, _ in rows}
            for (r1, v1), (r2, v2) in itertools.combinations(rows, 2):
                if persona[r1] == persona[r2] and v1 != v2:
                    out.append((r1, r2) if v1 > v2 else (r2, r1))
    return list(dict.fromkeys(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-pairs", action="store_true")
    ap.add_argument("--human-pairs", action="store_true")
    ap.add_argument("--persona", nargs="*")
    ap.add_argument("--one-order", action="store_true", help="judge each pair in one (random) order only")
    a = ap.parse_args()
    cfg = load_config()
    tmpl_hash = sha256_text(load_template("pairwise_judge.txt"))

    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RUNS_DIR.glob("*.json"))]
    runs = [r for r in runs if r.get("handoff_present") and not r.get("stress_test")]
    if a.persona:
        runs = [r for r in runs if r["persona"] in a.persona]
    done = set()  # only judgments made with the current template count as done
    if PAIR_CSV.exists():
        with open(PAIR_CSV, newline="", encoding="utf-8") as f:
            done = {(r["run_first"], r["run_second"]) for r in csv.DictReader(f) if r["template_sha256"] == tmpl_hash}

    if a.human_pairs:
        by_id = {r["run_id"]: r for r in runs}
        pairs = [(by_id[b], by_id[w]) for b, w in human_pairs() if b in by_id and w in by_id]
    else:
        pairs = pairs_for(runs, a.all_pairs)
    jobs = []
    for x, y in pairs:
        if a.one_order:  # deterministic coin flip per pair
            orders = [(x, y)] if sha256_text(x["run_id"] + y["run_id"])[0] < "8" else [(y, x)]
        else:
            orders = [(x, y), (y, x)]
        jobs += [o for o in orders if (o[0]["run_id"], o[1]["run_id"]) not in done]
    print(f"{len(jobs)} comparisons to run")
    new_file = not PAIR_CSV.exists()
    with open(PAIR_CSV, "a", newline="", encoding="utf-8") as fc, open(PAIR_JSONL, "a", encoding="utf-8") as fj:
        w = csv.DictWriter(fc, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        for first, second in jobs:
            check_budget(cfg)
            cid = "cmp_" + sha256_text(first["run_id"] + "|" + second["run_id"])[:10]
            try:
                j, cost = judge(cfg, first, second)
            except Exception as e:
                print(f"  {cid}: failed: {e}")
                continue
            ledger_add("compare_handoffs", cid, cost)
            winner_run = {"first": first["run_id"], "second": second["run_id"], "tie": "tie"}[j["overall_better"]]
            row = {"comparison_id": cid, "persona": first["persona"],
                   "run_first": first["run_id"], "run_second": second["run_id"],
                   "prompt_first": first["prompt"], "prompt_second": second["prompt"],
                   "run_n_first": first["run_n"], "run_n_second": second["run_n"],
                   "winner": j["overall_better"], "winner_run": winner_run, "strength": j["strength"],
                   "judge_model": cfg["roles"]["pairwise_judge"]["model"],
                   "judge_effort": cfg["roles"]["pairwise_judge"]["effort"], "template_sha256": tmpl_hash,
                   "timestamp": dt.datetime.now().isoformat(timespec="seconds"), "cost_usd": round(cost, 4),
                   "reason": j["reason"]}
            w.writerow(row)
            fc.flush()
            fj.write(json.dumps({**row, "criteria": j["criteria"]}, ensure_ascii=False) + "\n")
            fj.flush()
            print(f"done  {cid}: {first['prompt']}#{first['run_n']} vs {second['prompt']}#{second['run_n']} "
                  f"({first['persona']}) -> {j['overall_better']} ({j['strength']}) ${cost:.3f}")


if __name__ == "__main__":
    main()
