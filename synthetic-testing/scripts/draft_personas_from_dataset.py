"""Draft new personas from rows of the de-identified BHA dataset.

Reads a de-identified BHA survey export (row 0 = Qualtrics question text; not included in this
repository) and the Prompt Playbook data dictionary to label each column with its BHA domain. Before
anything is sent to a model it drops: TIN/EIN/SSN-like columns, demographics, household
income, administrative/derived fields, AEO's generated action/strength fields, and
links/identifiers. Drafts go to personas/drafts/ for review; move a draft up to
personas/ to use it in runs.

Usage:
  python scripts/draft_personas_from_dataset.py --dataset PATH --dictionary PATH --rows 12 57 --dry-run   # show what would be sent
  python scripts/draft_personas_from_dataset.py --dataset PATH --dictionary PATH --assign 12:sparse 57:detailed   # row:style
  python scripts/draft_personas_from_dataset.py --random 3 --seed 1

Styles: detailed, sparse, rambling, skeptical_of_ai. Each style also sets the persona's
owner-realism rates (STYLE_REALISM below); defaults come from config.yaml.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import pandas as pd
import yaml

from common import (FORBIDDEN_FIELD_RE, PERSONAS_DIR, call_model, check_budget, ledger_add, load_config,
                    load_template)

# Set with --dataset and --dictionary: the de-identified survey export and its data dictionary are not
# part of this repository. Never use a file that still contains TIN, EIN, SSN or other identifiers.
DATASET = None
DICTIONARY = None
DRAFTS = PERSONAS_DIR / "drafts"
STYLES = {
    "detailed": "detailed and reflective: full sentences, gives reasons, stays on the question",
    "sparse": "sparse and short: a few words, no reasons unless asked directly, never volunteers",
    "rambling": "rambling and sometimes off-topic: long, loosely organized answers that drift to side stories",
    "skeptical_of_ai": "plain-spoken and skeptical of AI: answers directly, but is wary of technology and of this "
                       "AI intake itself",
}
# Owner-realism overrides per style (anything not set here comes from config.yaml defaults).
STYLE_REALISM = {
    "detailed": {"partial_answer_rate": 0.35},
    "sparse": {"partial_answer_rate": 0.65, "unsure_rate": 0.045, "decline_rate": 0.015,
               "unsure_rate_sensitive": 0.1, "decline_rate_sensitive": 0.1},
    "rambling": {"partial_answer_rate": 0.4, "misunderstand_rate": 0.08, "tangent_rate": 0.25},
    "skeptical_of_ai": {"unsure_rate_sensitive": 0.07, "decline_rate_sensitive": 0.18},
}
CAPWORD_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)*")
FREE_TEXT_COL_RE = re.compile(r"_oe$|_open$|_TEXT$|^Business_Description_")
MODULE_KEYS = ["M1_welcome_consent", "M2_business_profile", "M3_stage_goals_vision", "M4_financial_confidence",
               "M5_capital_readiness", "M6_operations", "M7_products_inventory", "M8_sales_marketing",
               "M9_digital_ai", "M10_team_hr", "M11_legal_compliance", "M12_stressors_support", "M13_closing"]

EXCLUDE_DOMAINS = re.compile(r"Demographic|Administrative|Cross-cutting priorities", re.I)
EXCLUDE_COLS = re.compile(
    r"^(hispanic|race|gender|transgender|sexuality|age|education|disability|returning_citizen|active.?duty|"
    r"HHI_Contribution|diversity_income|results_view_link|disclaimer)|link|url|e-?mail|phone|address|zip|"
    r"latitude|longitude|ip_?address|response.?id|recipient|_ACTION_|^Business Strengths|^Business Opportunities", re.I)


def dictionary_index() -> list[tuple[str, dict]]:
    """[(column prefix, dictionary row)], longest prefixes first."""
    dd = pd.read_excel(DICTIONARY, sheet_name="Data Dictionary", dtype=str).fillna("")
    out = []
    for _, r in dd.iterrows():
        for part in re.split(r"\s*/\s*", r["Question ID / Variable"]):
            part = re.sub(r"\s*\(.*?\)", "", part).strip()
            m = re.match(r"^([!\w ]+?)_?\d+\.\.\d+$", part)          # e.g. Bo_stress_1..14
            prefix = (m.group(1) + "_") if m else part
            if prefix and "*" not in prefix:
                out.append((prefix, r.to_dict()))
    return sorted(out, key=lambda x: -len(x[0]))


def lookup(col: str, index) -> dict | None:
    for prefix, row in index:
        if col == prefix or (prefix.endswith("_") and col.startswith(prefix)) or col.startswith(prefix + "_"):
            return row
    return None


# Columns the dictionary doesn't map are AEO-derived (regions, bands, generated
# recommendation text such as Financial_N1); only the owner's own descriptions are kept.
UNMAPPED_KEEP = re.compile(r"^(Business_Description_|trans_valuation)")


def allowed(col: str, entry: dict | None) -> bool:
    if FORBIDDEN_FIELD_RE.search(col) or EXCLUDE_COLS.search(col):
        return False
    if entry is None:
        return bool(UNMAPPED_KEEP.search(col))
    return not EXCLUDE_DOMAINS.search(entry["BHA Domain"])


def row_answers(df: pd.DataFrame, i: int, index) -> list[str]:
    """Non-empty answers for data row i (1-based after the question-text row), grouped by domain."""
    questions, row = df.iloc[0], df.iloc[i]
    by_domain: dict[str, list[str]] = {}
    for col in df.columns:
        entry = lookup(col, index)
        if not allowed(col, entry):
            continue
        val = str(row[col]).strip()
        if not val or val.lower() == "nan":
            continue
        q = " ".join(str(questions[col]).split())
        if FORBIDDEN_FIELD_RE.search(q):
            continue
        domain = entry["BHA Domain"] if entry else "Unmapped"
        by_domain.setdefault(domain, []).append(f"- {q}: {val}")
    return [f"## {d}\n" + "\n".join(v) for d, v in by_domain.items()]


def schema() -> dict:
    """Flat list of facts tagged by module (a named key per module makes the grammar too large)."""
    fact = {"type": "object", "properties": {"module": {"type": "string", "enum": MODULE_KEYS},
                                             "fact": {"type": "string"},
                                             "src": {"type": "string", "enum": ["dataset", "invented"]}},
            "required": ["module", "fact", "src"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "label": {"type": "string"},
        "communication_style": {"type": "object", "properties": {
            "label": {"type": "string"}, "description": {"type": "string"}, "typical_reply_words": {"type": "string"}},
            "required": ["label", "description", "typical_reply_words"], "additionalProperties": False},
        "facts": {"type": "array", "items": fact}},
        "required": ["label", "communication_style", "facts"], "additionalProperties": False}


def group_facts(facts: list[dict]) -> dict:
    sheet = {k: [] for k in MODULE_KEYS}
    for f in facts:
        sheet[f["module"]].append({"fact": f["fact"], "src": f["src"], "invented": f["src"] == "invented"})
    return sheet


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, nargs="*", help="data row numbers (1 = first respondent)")
    ap.add_argument("--assign", nargs="*", help="row:style pairs, e.g. 12:sparse")
    ap.add_argument("--random", type=int, help="draft N random rows")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; no API call")
    ap.add_argument("--dataset", required=True, help="path to the de-identified BHA survey export (.xlsx)")
    ap.add_argument("--dictionary", required=True, help="path to the BHA Prompt Playbook data dictionary (.xlsx)")
    a = ap.parse_args()
    cfg = load_config()
    global DATASET, DICTIONARY
    DATASET, DICTIONARY = Path(a.dataset), Path(a.dictionary)

    df = pd.read_excel(DATASET, dtype=str, keep_default_na=False)
    n_resp = len(df) - 1
    styles: dict[int, str] = {}
    for pair in a.assign or []:
        row, style = pair.split(":")
        if style not in STYLES:
            ap.error(f"unknown style {style}; choose from {', '.join(STYLES)}")
        styles[int(row)] = style
    rows = list(styles) or a.rows or random.Random(a.seed).sample(range(1, n_resp + 1), a.random or 1)
    index = dictionary_index()
    DRAFTS.mkdir(parents=True, exist_ok=True)

    for i in rows:
        answers = row_answers(df, i, index)
        style = styles.get(i, "detailed")
        msg = (f"ASSIGNED COMMUNICATION STYLE: {STYLES[style]}\n\n"
               f"SURVEY ANSWERS (respondent row {i})\n\n" + "\n\n".join(answers))
        # Capitalized words inside free text, for a manual check that no real names leak through.
        capwords = set()
        for col in df.columns:
            if FREE_TEXT_COL_RE.search(col) and allowed(col, lookup(col, index)):
                capwords |= set(CAPWORD_RE.findall(str(df.iloc[i][col])))
        capwords = sorted(capwords)
        if a.dry_run:
            print(msg[:6000] + ("\n..." if len(msg) > 6000 else ""))
            print("capitalized words in free text:", capwords)
            continue
        check_budget(cfg)
        _, text, usage = call_model(cfg, "persona_drafter", load_template("persona_drafter.txt"),
                                    [{"role": "user", "content": msg}],
                                    output_format={"type": "json_schema", "schema": schema()})
        ledger_add("draft_persona", f"row{i}", usage["cost_usd"])
        p = json.loads(text)
        pid = f"d_row{i:04d}"
        sheet = group_facts(p["facts"])
        persona = {"persona_id": pid, "label": p["label"], "consents": True, "source_row": i, "style_key": style,
                   "communication_style": p["communication_style"], "realism": STYLE_REALISM[style],
                   "extra_instructions": "", "fact_sheet": sheet}
        n_inv = sum(f["invented"] for fs in sheet.values() for f in fs)
        n_all = sum(len(fs) for fs in sheet.values())
        empty = [k for k, v in sheet.items() if not v]
        if empty:
            print(f"  WARNING {pid}: no facts for {empty}")
        path = DRAFTS / f"{pid}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# DRAFT persona from {DATASET.name}, data row {i}. Review before use.\n"
                    f"# src: dataset = from the survey answers; invented = filled in by {cfg['roles']['persona_drafter']['model']}.\n"
                    f"# {n_inv} of {n_all} facts invented.\n\n")
            yaml.safe_dump(persona, f, sort_keys=False, allow_unicode=True, width=110)
        print(f"wrote {path.name} ({style}): {n_all} facts ({n_inv} invented), ${usage['cost_usd']:.3f}")
        if capwords:
            print(f"  check for leaked names; capitalized words in this row's free text: {capwords}")


if __name__ == "__main__":
    main()
