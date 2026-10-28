"""Blind rubric scoring of each run.

The grader sees the persona fact sheet, the transcript and the handoff under an
anonymous case ID; it is never told the prompt version. Writes one row per run to
scores/scores.csv and the full write-up to scores/full/<run_id>.md.

Usage:
  python scripts/score_run.py              # every run not yet scored
  python scripts/score_run.py --force ID   # re-score specific runs
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json

from common import (RUBRIC_DIR, RUNS_DIR, SCORES_DIR, call_model, check_budget, fact_sheet_text, ledger_add,
                    load_config, load_persona, load_template, sha256_text)

SCORES_CSV = SCORES_DIR / "scores.csv"
A_ITEMS = [f"A{i}" for i in range(1, 11)]
B_ITEMS = [f"B{i}" for i in range(1, 13) if i != 11]          # B11 is N/A in synthetic runs
ITEM_NAMES = {
    "A1": "Natural and respectful conversation flow", "A2": "Clear and understandable question wording",
    "A3": "Efficient question sequence", "A4": "Collects valuable advising-relevant information",
    "A5": "Balances structured questions with open-ended insight", "A6": "Uses follow-up questions effectively",
    "A7": "Avoids unnecessary or burdensome probing", "A8": "Uses skip logic appropriately",
    "A9": "Avoids premature advice", "A10": "Overall quality of intake experience",
    "B1": "Accurate structured data capture", "B2": "Correct handling of skipped, missing, or unclear fields",
    "B3": "Preserves useful narrative detail", "B4": "Accurate business snapshot",
    "B5": "Specific and grounded strengths", "B6": "Useful opportunities / areas for support",
    "B7": "Appropriate top 3 possible priorities", "B8": "Evidence-based interpretation",
    "B9": "Helpful advisor follow-up questions", "B10": "Clear guardrails and no overreach",
    "B12": "Overall usefulness for advisor preparation",
}
GUARDRAILS = ["advice_given", "products_recommended", "promised_results", "invented_details",
              "missed_red_flags", "consent_skipped"]
SECTIONS = ["disclaimer", "business_snapshot", "stated_goals", "strengths", "opportunities",
            "ai_tech_opportunities", "top3_priorities", "evidence_for_priorities", "followup_questions",
            "missing_or_unclear", "watchouts_referrals", "structured_intake_record"]
SIM_CHECKS = ["owner_broke_character", "owner_contradicted_fact_sheet", "owner_volunteered_unasked",
              "conversation_stalled_or_looped"]


def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def grader_schema() -> dict:
    """Arrays keyed by an enum id (a named key per item makes the grammar too large for the API)."""
    def arr(id_enum: list[str], props: dict) -> dict:
        return {"type": "array", "items": obj({"id": {"type": "string", "enum": id_enum}, **props})}
    flag = {"flag": {"type": "boolean"}, "quote": {"type": "string"}, "explanation": {"type": "string"}}
    return obj({
        "weakest_element": {"type": "string"},
        "scores": arr(A_ITEMS + B_ITEMS, {"score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                                          "reasoning": {"type": "string"}, "justification": {"type": "string"}}),
        "guardrails": arr(GUARDRAILS, flag),
        "sections_present": arr(SECTIONS, {"present": {"type": "boolean"}}),
        "simulation_checks": arr(SIM_CHECKS, flag),
        "overall_comment": {"type": "string"},
    })


def to_maps(raw: dict) -> dict:
    """Convert the grader's arrays to {id: entry} maps; raise if any id is missing."""
    g = {"overall_comment": raw["overall_comment"], "weakest_element": raw.get("weakest_element", "")}
    for key, ids in (("scores", A_ITEMS + B_ITEMS), ("guardrails", GUARDRAILS),
                     ("sections_present", SECTIONS), ("simulation_checks", SIM_CHECKS)):
        m = {e["id"]: e for e in raw[key]}
        missing = [i for i in ids if i not in m]
        if missing:
            raise ValueError(f"grader omitted {key}: {missing}")
        g[key] = {i: m[i] for i in ids}
    g["sections_present"] = {k: v["present"] for k, v in g["sections_present"].items()}
    return g


def case_message(run: dict, persona: dict) -> str:
    lines = []
    for t in run["turns"]:
        body = "[advisor handoff: see ADVISOR HANDOFF below]" if t.get("is_handoff") else t["text"]
        lines.append(f"[turn {t['turn']}] {t['speaker'].upper()}: {body}")
    handoff = run.get("handoff_text") or "(no handoff was produced)"
    return (f"CASE ID: {run['blind_id']}\n\n"
            f"OWNER FACT SHEET ({persona['label']}; communication style: {persona['communication_style']['label']})\n"
            f"{fact_sheet_text(persona)}\n\n"
            f"TRANSCRIPT\n" + "\n\n".join(lines) + "\n\n"
            f"ADVISOR HANDOFF\n<document>\n{handoff}\n</document>")


def yn(b: bool) -> str:
    return "yes" if b else "no"


def write_full(run: dict, g: dict, meta: dict) -> None:
    out = [f"# Grader write-up: {run['run_id']}", "",
           f"Blind case ID shown to grader: `{run['blind_id']}`. Grader: {meta['grader_model']} "
           f"(effort {meta['grader_effort']}), rubric sha256 `{meta['rubric_sha256'][:12]}`, {meta['timestamp']}.", "",
           "## Overall", "", g["overall_comment"], "",
           f"**Weakest element of the handoff:** {g['weakest_element']}", ""]
    for sec, items in (("Section A — Conversational intake and information quality", A_ITEMS),
                       ("Section B — Structured record and advisor-facing output quality", B_ITEMS)):
        out += [f"## {sec}", ""]
        for k in items:
            s = g["scores"][k]
            out += [f"### {k} {ITEM_NAMES[k]}: {s['score']}/5", "", f"**Justification:** {s['justification']}", "",
                    s["reasoning"], ""]
        if items is B_ITEMS:
            out += ["### B11 Similar or stronger usefulness than current BHA-style output: N/A", "",
                    "Not scored in synthetic runs (no BHA benchmark output).", ""]
    out += ["## Guardrail flags", ""]
    for k in GUARDRAILS:
        f = g["guardrails"][k]
        out += [f"- **{k}: {yn(f['flag'])}**" + (f": \"{f['quote']}\". {f['explanation']}" if f["flag"] else "")]
    out += ["", "## Handoff sections", ""]
    out += [f"- {k}: {'present' if v else '**missing**'}" for k, v in g["sections_present"].items()]
    out += ["", "## Simulation checks (simulated owner)", ""]
    for k in SIM_CHECKS:
        f = g["simulation_checks"][k]
        out += [f"- **{k}: {yn(f['flag'])}**" + (f": \"{f['quote']}\". {f['explanation']}" if f["flag"] else "")]
    (SCORES_DIR / "full" / f"{run['run_id']}.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def csv_row(run: dict, g: dict, meta: dict) -> dict:
    row = {"run_id": run["run_id"], "blind_id": run["blind_id"], "prompt": run["prompt"], "persona": run["persona"],
           "run_n": run["run_n"], **meta,
           "stop_reason": run["stop_reason"], "n_turns": run["n_turns"], "n_exchanges": run["n_exchanges"],
           "owner_word_count": run["owner_word_count"]}
    for k in A_ITEMS + B_ITEMS:
        row[k] = g["scores"][k]["score"]
    row["A_mean"] = round(sum(row[k] for k in A_ITEMS) / len(A_ITEMS), 3)
    row["B_mean"] = round(sum(row[k] for k in B_ITEMS) / len(B_ITEMS), 3)
    for k in GUARDRAILS:
        row[f"flag_{k}"] = yn(g["guardrails"][k]["flag"])
        row[f"quote_{k}"] = g["guardrails"][k]["quote"] if g["guardrails"][k]["flag"] else ""
    for k in SECTIONS:
        row[f"sec_{k}"] = yn(g["sections_present"][k])
    row["sections_missing"] = ";".join(k for k in SECTIONS if not g["sections_present"][k])
    row["weakest_element"] = g["weakest_element"]
    for k in SIM_CHECKS:
        row[f"sim_{k}"] = yn(g["simulation_checks"][k]["flag"])
    return row


def score(cfg: dict, run: dict) -> dict:
    rubric = (RUBRIC_DIR / "rubric.md").read_text(encoding="utf-8")
    system = load_template("grader.txt").format(rubric=rubric)
    persona = load_persona(run["persona"])
    cost = 0.0
    for attempt in (1, 2):  # one retry if the grader leaves out an item
        _, text, usage = call_model(cfg, "grader", system, [{"role": "user", "content": case_message(run, persona)}],
                                    output_format={"type": "json_schema", "schema": grader_schema()})
        cost += usage["cost_usd"]
        try:
            g = to_maps(json.loads(text))
            break
        except ValueError as e:
            if attempt == 2:
                ledger_add("score_run", run["run_id"], cost)
                raise
            print(f"  {run['run_id']}: {e}; retrying")
    s = cfg["roles"]["grader"]
    meta = {"grader_model": s["model"], "grader_effort": s["effort"], "rubric_sha256": sha256_text(rubric),
            "grader_template_sha256": sha256_text(load_template("grader.txt")),
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"), "grader_cost_usd": round(cost, 4)}
    ledger_add("score_run", run["run_id"], cost)
    write_full(run, g, meta)
    row = csv_row(run, g, meta)
    print(f"done  {run['run_id']}: A={row['A_mean']} B={row['B_mean']} ${cost:.3f}")
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--force", action="store_true", help="re-score runs that already have a row")
    a = ap.parse_args()
    cfg = load_config()
    (SCORES_DIR / "full").mkdir(parents=True, exist_ok=True)

    existing = []
    if SCORES_CSV.exists():
        with open(SCORES_CSV, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    scored = {r["run_id"] for r in existing}
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RUNS_DIR.glob("*.json"))]
    runs = [r for r in runs if not r.get("stress_test")]  # stress runs are scored by score_stress.py
    if a.run_ids:
        runs = [r for r in runs if r["run_id"] in a.run_ids]
    if not a.force:
        runs = [r for r in runs if r["run_id"] not in scored]
    if not runs:
        print("nothing to score")
        return

    new = []
    for run in runs:
        check_budget(cfg)
        try:
            new.append(score(cfg, run))
        except Exception as e:
            print(f"  {run['run_id']}: grading failed: {e}")
    redone = {r["run_id"] for r in new}
    rows = [r for r in existing if r["run_id"] not in redone] + new
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(SCORES_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(new)} rows to {SCORES_CSV.name}")


if __name__ == "__main__":
    main()
