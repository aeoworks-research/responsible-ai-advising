"""Pull the Structured Intake Record out of each run's advisor handoff.

1. Extractor (cheap model) sees ONLY the handoff text, never the transcript, and copies
   the assistant's record into the prompt's own field list.
2. Fields that are not in a prompt's template are written as not_in_template.
3. Checker codes each captured/unclear value against the persona fact sheet.
4. Rows replace any earlier rows for the same run in data/structured_records_long.csv,
   then data/structured_records_wide.xlsx is rebuilt from the long file.

Usage:
  python scripts/extract_record.py              # every run not yet extracted
  python scripts/extract_record.py --all        # re-extract every run
  python scripts/extract_record.py RUN_ID ...   # specific runs
"""
from __future__ import annotations

import argparse
import csv
import json

import pandas as pd

from common import (DATA_DIR, PROMPTS_DIR, RUNS_DIR, call_model, check_budget, fact_sheet_text, ledger_add,
                    load_config, load_persona, load_template, load_yaml, sum_usage)

LONG_CSV = DATA_DIR / "structured_records_long.csv"
WIDE_XLSX = DATA_DIR / "structured_records_wide.xlsx"
COLUMNS = ["run_id", "prompt", "persona", "run_n", "category", "field", "value", "status", "accuracy",
           "extraction_flag", "accuracy_note", "discrepancy_source"]
ACCURACY = ["correct", "partly_correct", "wrong", "invented", "not_checkable"]
# A record missing more than this share of its template fields is flagged incomplete.
INCOMPLETE_SHARE = 0.2


def template_fields(prompt: str) -> list[tuple[str, str]]:
    cats = load_yaml(PROMPTS_DIR / f"{prompt}.fields.yaml") or []
    return [(c["category"], f) for c in cats for f in c["fields"]]


def all_fields() -> list[tuple[str, str]]:
    """Union of fields across all registered prompts, in first-seen order."""
    registry = load_yaml(PROMPTS_DIR / "registry.yaml")
    seen, out = set(), []
    for name in registry:
        path = PROMPTS_DIR / f"{name}.fields.yaml"
        if not path.exists():
            continue
        for cf in template_fields(name):
            if cf not in seen:
                seen.add(cf)
                out.append(cf)
    return out


def extractor_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "record_found": {"type": "boolean"},
            "fields": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"}, "field": {"type": "string"}, "value": {"type": "string"},
                    "status": {"type": "string", "enum": ["captured", "skipped", "missing", "unclear", "not_found"]},
                },
                "required": ["category", "field", "value", "status"], "additionalProperties": False}},
        },
        "required": ["record_found", "fields"], "additionalProperties": False,
    }


CHECK_SCHEMA = {
    "type": "object",
    "properties": {"checks": {"type": "array", "items": {
        "type": "object",
        "properties": {"category": {"type": "string"}, "field": {"type": "string"},
                       "accuracy": {"type": "string", "enum": ACCURACY}, "note": {"type": "string"},
                       "discrepancy_source": {"type": "string", "enum": ["none", "owner_said_it", "assistant"]}},
        "required": ["category", "field", "accuracy", "note", "discrepancy_source"], "additionalProperties": False}}},
    "required": ["checks"], "additionalProperties": False,
}


def extract(cfg: dict, handoff: str, fields: list[tuple[str, str]]):
    field_list = "\n".join(f"- {c} :: {f}" for c, f in fields)
    msg = (f"FIELD LIST (category :: field)\n{field_list}\n\n"
           f"ADVISOR HANDOFF DOCUMENT\n<document>\n{handoff}\n</document>")
    _, text, usage = call_model(cfg, "record_extractor", load_template("record_extractor.txt"),
                                [{"role": "user", "content": msg}],
                                output_format={"type": "json_schema", "schema": extractor_schema()})
    return json.loads(text), usage


def check(cfg: dict, persona: dict, items: list[dict], owner_turns: list[str]):
    values = "\n".join(f"- {i['category']} :: {i['field']} = {i['value']}" for i in items)
    answers = "\n".join(f"- {t}" for t in owner_turns)
    msg = (f"FACT SHEET\n{fact_sheet_text(persona)}\n\n"
           f"OWNER'S ANSWERS (owner turns only, in order)\n{answers}\n\n"
           f"VALUES TO CHECK (category :: field = value)\n{values}")
    _, text, usage = call_model(cfg, "accuracy_checker", load_template("accuracy_checker.txt"),
                                [{"role": "user", "content": msg}],
                                output_format={"type": "json_schema", "schema": CHECK_SCHEMA})
    return {(c["category"], c["field"]): c for c in json.loads(text)["checks"]}, usage


def process_run(cfg: dict, run: dict) -> list[dict]:
    prompt, run_id = run["prompt"], run["run_id"]
    in_template = template_fields(prompt)
    in_template_set = set(in_template)
    base = {"run_id": run_id, "prompt": prompt, "persona": run["persona"], "run_n": run["run_n"]}
    run_flags: list[str] = []
    usages = []

    handoff = run.get("handoff_text", "")
    extracted: dict = {}
    if not handoff.strip():
        run_flags.append("record_missing")
    else:
        if not run.get("handoff_complete"):
            run_flags.append("handoff_incomplete")
        try:
            out, u = extract(cfg, handoff, in_template)
            usages.append(u)
            if not out["record_found"]:
                run_flags.append("record_missing")
            extracted = {(f["category"], f["field"]): f for f in out["fields"]}
        except Exception as e:
            print(f"  {run_id}: extraction failed: {e}")
            run_flags.append("record_malformed")

    rows = []
    omitted = 0
    for cat, field in all_fields():
        row = {**base, "category": cat, "field": field, "value": "", "status": "", "accuracy": "",
               "extraction_flag": "", "accuracy_note": "", "discrepancy_source": ""}
        if (cat, field) not in in_template_set:
            row["status"] = "not_in_template"
        elif extracted:
            e = extracted.get((cat, field))
            if e is None:
                row["status"], row["extraction_flag"] = "missing", "extractor_skipped_field"
            elif e["status"] == "not_found":
                row["status"], row["extraction_flag"] = "missing", "omitted_by_assistant"
                omitted += 1
            else:
                row["value"], row["status"] = e["value"].strip(), e["status"]
        rows.append(row)

    if extracted and in_template and omitted / len(in_template) > INCOMPLETE_SHARE:
        run_flags.append("record_incomplete")

    to_check = [r for r in rows if r["status"] in ("captured", "unclear") and r["value"]]
    if to_check:
        try:
            owner_turns = [t["text"] for t in run["turns"] if t["speaker"] == "owner" and not t.get("fixed_opening")]
            checks, u = check(cfg, load_persona(run["persona"]), to_check, owner_turns)
            usages.append(u)
            for r in to_check:
                c = checks.get((r["category"], r["field"]))
                if c:
                    r["accuracy"], r["accuracy_note"] = c["accuracy"], c["note"]
                    r["discrepancy_source"] = c["discrepancy_source"]
                else:
                    r["extraction_flag"] = ";".join(x for x in (r["extraction_flag"], "accuracy_not_returned") if x)
        except Exception as e:
            print(f"  {run_id}: accuracy check failed: {e}")
            run_flags.append("accuracy_check_failed")

    if run_flags:
        for r in rows:
            r["extraction_flag"] = ";".join(x for x in run_flags + [r["extraction_flag"]] if x)

    cost = sum_usage(usages)["total_cost_usd"] if usages else 0.0
    ledger_add("extract_record", run_id, cost)
    print(f"done  {run_id}: {len(in_template)} template fields, flags={run_flags or 'none'}, ${cost:.3f}")
    return rows


def write_long(new_rows: list[dict], run_ids: set[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    old = []
    if LONG_CSV.exists():
        with open(LONG_CSV, newline="", encoding="utf-8") as f:
            old = [r for r in csv.DictReader(f) if r["run_id"] not in run_ids]
    with open(LONG_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(old + new_rows)


def rebuild_wide() -> None:
    """One row per run; value/status/accuracy columns per field. Always rebuilt from the long file."""
    df = pd.read_csv(LONG_CSV, dtype=str, keep_default_na=False)
    df["key"] = df["category"].str.replace(r"[^A-Za-z]+", "_", regex=True).str.strip("_") + "." + df["field"]
    order = list(dict.fromkeys(df["key"]))
    idx = ["run_id", "prompt", "persona", "run_n"]
    wide = df.pivot(index=idx, columns="key", values=["value", "status", "accuracy"])
    wide.columns = [f"{k}.{what}" for what, k in wide.columns]
    cols = [f"{k}.{what}" for k in order for what in ("value", "status", "accuracy")]
    wide = wide[cols].reset_index()
    flags = df.groupby("run_id")["extraction_flag"].apply(
        lambda s: ";".join(sorted({x for v in s for x in v.split(";") if x and x not in
                                   ("omitted_by_assistant", "extractor_skipped_field", "accuracy_not_returned")})))
    wide.insert(4, "run_flags", wide["run_id"].map(flags))
    with pd.ExcelWriter(WIDE_XLSX, engine="openpyxl") as xw:
        wide.to_excel(xw, sheet_name="records", index=False)
        pd.DataFrame({"note": [
            "Generated by scripts/extract_record.py from structured_records_long.csv. Do not edit by hand; it is overwritten.",
            "status: captured / unclear / missing / skipped (assistant marked not applicable) / not_in_template.",
            "accuracy (vs persona fact sheet): correct / partly_correct / wrong / invented / not_checkable; blank if no value.",
        ]}).to_excel(xw, sheet_name="readme", index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    cfg = load_config()

    done = set()
    if LONG_CSV.exists():
        with open(LONG_CSV, newline="", encoding="utf-8") as f:
            done = {r["run_id"] for r in csv.DictReader(f)}
    paths = sorted(p for p in RUNS_DIR.glob("*.json"))
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    runs = [r for r in runs if not r.get("stress_test")]  # stress runs are scored by score_stress.py
    if a.run_ids:
        runs = [r for r in runs if r["run_id"] in a.run_ids]
    elif not a.all:
        runs = [r for r in runs if r["run_id"] not in done]
    if not runs:
        print("nothing to extract")
    rows, ids = [], set()
    for run in runs:
        check_budget(cfg)
        rows += process_run(cfg, run)
        ids.add(run["run_id"])
    if rows:
        write_long(rows, ids)
    if LONG_CSV.exists():
        rebuild_wide()
        print(f"wrote {LONG_CSV.name} and {WIDE_XLSX.name}")


if __name__ == "__main__":
    main()
