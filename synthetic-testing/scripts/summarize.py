"""Short markdown report comparing prompt versions -> reports/summary.md.

Uncertainty: personas are the unit of replication. Prompt differences are paired by
persona (mean of a persona's runs under each prompt), with a t-interval over personas
(df = personas − 1). Run-level SDs are shown for spread. With 3 personas and 2 runs per
cell everything here is descriptive. Flag rates get Wilson 95% intervals.
Human-vs-grader agreement is included once human_scoring/*/scoring_sheet.xlsx has scores.
"""
from __future__ import annotations

import csv
import json
import math

import numpy as np
import pandas as pd
from scipy import stats

from common import (DATA_DIR, HUMAN_DIR, PROMPTS_DIR, RUNS_DIR, SCORES_DIR, ST_ROOT, ledger_total, load_config,
                    load_yaml, sha256_text)
from export_for_human_scoring import HUMAN_ITEMS
from score_run import A_ITEMS, B_ITEMS, GUARDRAILS, ITEM_NAMES, SIM_CHECKS

REPORT = ST_ROOT / "reports" / "summary.md"
# Template meta-fields that describe the record rather than the business; left out of capture rates.
META_RECORD_CATEGORIES = {"Missing or Unclear Information"}


def mean_sd(x: pd.Series) -> str:
    x = x.astype(float)
    return f"{x.mean():.2f} (SD {x.std(ddof=1):.2f})" if len(x) > 1 else f"{x.mean():.2f}"


def paired_diffs(df: pd.DataFrame, col: str, a: str, b: str) -> pd.Series:
    """Per-persona difference b − a of cell means."""
    cell = df.groupby(["persona", "prompt"])[col].mean().unstack("prompt")
    return (cell[b] - cell[a]).dropna() if a in cell and b in cell else pd.Series(dtype=float)


def t_ci(d: pd.Series) -> tuple[float, float, float]:
    est = float(d.mean()) if len(d) else math.nan
    if len(d) < 2:
        return est, math.nan, math.nan
    h = stats.t.ppf(0.975, len(d) - 1) * d.std(ddof=1) / math.sqrt(len(d))
    return est, est - h, est + h


def wilson(k: int, n: int) -> str:
    if n == 0:
        return "–"
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{k}/{n} ({max(0, c - h):.0%}–{min(1, c + h):.0%})"


def fmt(est, lo, hi, d=2) -> str:
    if math.isnan(lo):
        return f"{est:.{d}f}"
    return f"{est:.{d}f} [{lo:.{d}f}, {hi:.{d}f}]"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |")
    return "\n".join(lines)


def section_scores(scores: pd.DataFrame, prompts: list[str]) -> list[str]:
    out = ["## Rubric scores (grader, blind)", "",
           "Section means on the 1–5 scale (mean of item scores per run), run-level mean and SD.", ""]
    rows = []
    for p in prompts:
        d = scores[scores.prompt == p]
        rows.append({"prompt": p, "n runs": len(d), "personas": d.persona.nunique(),
                     "Section A": mean_sd(d.A_mean), "Section B": mean_sd(d.B_mean)})
    out += [md_table(pd.DataFrame(rows)), ""]
    a = prompts[0]
    for b in prompts[1:]:
        da, db = paired_diffs(scores, "A_mean", a, b), paired_diffs(scores, "B_mean", a, b)
        if not len(da):
            continue
        per = pd.DataFrame({"persona": da.index, "Δ Section A": da.round(2).values, "Δ Section B": db.round(2).values})
        out += [f"**Paired by persona ({b} − {a}):** Section A {fmt(*t_ci(da))}; Section B {fmt(*t_ci(db))} "
                f"(mean of {len(da)} persona-level differences, 95% t-interval, df = {len(da) - 1}). "
                f"Positive in {int((da > 0).sum())}/{len(da)} personas for A and {int((db > 0).sum())}/{len(db)} for B.",
                "", md_table(per), ""]
    items = []
    for k in A_ITEMS + B_ITEMS:
        r = {"item": f"{k} {ITEM_NAMES[k]}"}
        for p in prompts:
            r[p] = f"{scores.loc[scores.prompt == p, k].astype(float).mean():.2f}"
        items.append(r)
    out += ["<details><summary>Item means by prompt</summary>", "", md_table(pd.DataFrame(items)), "", "</details>", ""]
    return out


def load_pairwise() -> pd.DataFrame | None:
    """Side-by-side judgments made with the current judge template only (never mix versions)."""
    path = SCORES_DIR / "pairwise.csv"
    if not path.exists():
        return None
    pw = pd.read_csv(path, dtype=str, keep_default_na=False)
    current = sha256_text((ST_ROOT / "templates" / "pairwise_judge.txt").read_text(encoding="utf-8"))
    return pw[pw.template_sha256 == current]


def run_win_rates(pw: pd.DataFrame) -> pd.Series:
    """Share of comparisons each run won (ties count half), over all comparisons it appears in."""
    pts: dict[str, list[float]] = {}
    for _, r in pw.iterrows():
        for run in (r.run_first, r.run_second):
            pts.setdefault(run, []).append(1.0 if r.winner_run == run else 0.5 if r.winner_run == "tie" else 0.0)
    return pd.Series({k: float(np.mean(v)) for k, v in pts.items()})


def section_pairwise(pw: pd.DataFrame, prompts: list[str]) -> list[str]:
    out = ["## Side-by-side handoff comparison (blind, same persona, both orders)", ""]
    cross = pw[(pw.prompt_first != pw.prompt_second) & (pw.run_n_first == pw.run_n_second)]
    out += [f"Position check: the first-shown handoff won {np.mean(pw.winner == 'first'):.0%} of "
            f"{len(pw)} comparisons, ties {np.mean(pw.winner == 'tie'):.0%}.", ""]
    if not len(cross):
        return out + ["_No cross-prompt comparisons yet._", ""]
    a = prompts[0]
    for b in prompts[1:]:
        c = cross[cross.prompt_first.isin([a, b]) & cross.prompt_second.isin([a, b])]
        if not len(c):
            continue
        c = c.assign(b_pts=[1.0 if (r.prompt_first == b and r.winner == "first") or
                            (r.prompt_second == b and r.winner == "second") else 0.5 if r.winner == "tie" else 0.0
                            for _, r in c.iterrows()])
        per = c.groupby("persona").b_pts.mean()
        est, lo, hi = t_ci(per - 0.5)
        out += [f"**{b} vs {a}:** {b} preferred in {c.b_pts.mean():.0%} of {len(c)} comparisons. "
                f"Persona-level win rate minus 50%: {est:+.0%} [{lo:+.0%}, {hi:+.0%}] (t-interval, df = {len(per) - 1}); "
                f"{b} ahead in {int((per > 0.5).sum())}/{len(per)} personas, behind in {int((per < 0.5).sum())}.", "",
                md_table(pd.DataFrame({"persona": per.index, f"{b} win rate": [f"{v:.0%}" for v in per.values],
                                       "comparisons": c.groupby("persona").size().values})), ""]
    return out


def section_flags(scores: pd.DataFrame, prompts: list[str]) -> list[str]:
    out = ["## Guardrail flags (runs flagged / runs, Wilson 95% interval)", ""]
    rows = []
    for g in GUARDRAILS:
        r = {"flag": g}
        for p in prompts:
            d = scores[scores.prompt == p]
            r[p] = wilson(int((d[f"flag_{g}"] == "yes").sum()), len(d))
        rows.append(r)
    out += [md_table(pd.DataFrame(rows)), "", "Quotes are in `scores/full/<run_id>.md` and `scores/scores.csv`.", ""]
    return out


def section_length(runs: pd.DataFrame, prompts: list[str]) -> list[str]:
    out = ["## Run length", ""]
    rows = []
    for p in prompts:
        d = runs[runs.prompt == p]
        rows.append({"prompt": p, "runs": len(d),
                     "exchanges, mean (min–max)": f"{d.n_exchanges.mean():.1f} ({d.n_exchanges.min()}–{d.n_exchanges.max()})",
                     "owner words, mean": f"{d.owner_word_count.mean():.0f}",
                     "assistant words excl. handoff, mean": f"{d.assistant_words.mean():.0f}",
                     "stop reasons": "; ".join(f"{k} {v}" for k, v in d.stop_reason.value_counts().items()),
                     "cost/run": f"${d.cost.mean():.2f}"})
    out += [md_table(pd.DataFrame(rows)), ""]
    return out


def section_records(long: pd.DataFrame, prompts: list[str]) -> list[str]:
    out = ["## Structured-record capture", "",
           "Shares are of fields in each prompt's own template (not_in_template rows excluded). "
           "Accuracy is among values that could be checked against the fact sheet (excludes not_checkable). "
           "'Due to assistant' excludes mismatches that trace to something the simulated owner said.", ""]
    if "discrepancy_source" not in long:
        long = long.assign(discrepancy_source="")
    out[-2] += (" The template's own meta-category (" + ", ".join(sorted(META_RECORD_CATEGORIES)) +
                ") describes the record rather than the business and is left out.")
    t = long[(long.status != "not_in_template") & ~long.category.isin(META_RECORD_CATEGORIES)].copy()
    t["missing_or_unclear"] = t.status.isin(["missing", "unclear"])
    chk = t[t.accuracy.isin(["correct", "partly_correct", "wrong", "invented"])]

    def acc(d):
        n = len(d)
        if not n:
            return "–"
        c = (d.accuracy == "correct").sum()
        bad = d.accuracy.isin(["wrong", "invented"]).sum()
        asst = ((d.accuracy != "correct") & (d.discrepancy_source == "assistant")).sum()
        return f"{c / n:.0%} correct, {bad / n:.0%} wrong/invented, {asst / n:.0%} not correct due to assistant (n={n})"

    rows = []
    for p in prompts:
        d, dc = t[t.prompt == p], chk[chk.prompt == p]
        sc = d.status.value_counts(normalize=True)
        rows.append({"prompt": p, "field rows": len(d), "captured": f"{sc.get('captured', 0):.0%}",
                     "unclear": f"{sc.get('unclear', 0):.0%}", "missing": f"{sc.get('missing', 0):.0%}",
                     "skipped": f"{sc.get('skipped', 0):.0%}", "accuracy": acc(dc)})
    out += [md_table(pd.DataFrame(rows)), ""]

    flagged = long[long.extraction_flag.str.contains("record_", na=False)].run_id.unique()
    if len(flagged):
        out += [f"**Runs with record problems:** {', '.join(flagged)}", ""]

    cat_rows = []
    for c in dict.fromkeys(t.category):
        r = {"category": c}
        for p in prompts:
            d, dc = t[(t.category == c) & (t.prompt == p)], chk[(chk.category == c) & (chk.prompt == p)]
            if not len(d):
                r[p] = "–"
                continue
            r[p] = f"{d.missing_or_unclear.mean():.0%} missing/unclear" + (
                f"; {(dc.accuracy == 'correct').mean():.0%} correct" if len(dc) else "")
        cat_rows.append(r)
    out += ["### By category", "", md_table(pd.DataFrame(cat_rows)), ""]

    f_rows = []
    for (c, f), d in t.groupby(["category", "field"], sort=False):
        r = {"field": f"{c} :: {f}"}
        for p in prompts:
            dp = d[d.prompt == p]
            dc = dp[dp.accuracy.isin(["correct", "partly_correct", "wrong", "invented"])]
            if not len(dp):
                r[p] = "not in template"
                continue
            r[p] = f"{dp.missing_or_unclear.mean():.0%} m/u" + (f", {(dc.accuracy == 'correct').mean():.0%} ok" if len(dc) else "")
        f_rows.append(r)
    out += ["### By field", "", "m/u = missing or unclear; ok = correct among checkable values.", "",
            "<details><summary>All fields</summary>", "", md_table(pd.DataFrame(f_rows)), "", "</details>", ""]

    ai = long[long.field == "current_ai_use"]
    if len(ai):
        out += ["### current_ai_use", ""]
        out += [md_table(ai[["run_id", "status", "accuracy", "value"]].assign(
            value=lambda x: x.value.str.slice(0, 120)).reset_index(drop=True)), ""]
    return out


def section_sim(scores: pd.DataFrame) -> list[str]:
    out = ["## Simulation checks (simulated owner)", ""]
    rows = [{"check": k, "runs flagged": f"{int((scores[f'sim_{k}'] == 'yes').sum())}/{len(scores)}"} for k in SIM_CHECKS]
    return out + [md_table(pd.DataFrame(rows)), ""]


def section_agreement(scores: pd.DataFrame) -> list[str]:
    sheets = sorted(HUMAN_DIR.glob("batch_*/scoring_sheet.xlsx"))
    pairs = []
    for sheet in sheets:
        key = {r["sample_id"]: r["run_id"] for r in csv.DictReader(open(sheet.parent / "_key.csv", encoding="utf-8"))}
        h = pd.read_excel(sheet, sheet_name="scores", dtype=str).fillna("")
        for _, r in h.iterrows():
            run_id = key.get(r["sample_id"])
            g = scores[scores.run_id == run_id]
            if run_id and len(g):
                pairs.append((r, g.iloc[0]))
    def has(h, col):
        return col in h and str(h[col]).strip() != ""

    out = ["## Human vs. grader agreement", ""]
    if not any(has(h, c) for h, g in pairs for c in [*HUMAN_ITEMS, "picked_best", "guardrail_concern"]):
        return out + ["No human judgments yet. Run `scripts/export_for_human_scoring.py`, fill in "
                      "`scoring_sheet.xlsx`, then re-run this script.", ""]

    # Two holistic scores, each against the grader's matching item and its section mean.
    rows = []
    for col, (gid, _) in HUMAN_ITEMS.items():
        sub = [(float(h[col]), g) for h, g in pairs if has(h, col)]
        if not sub:
            continue
        hv = np.array([x for x, _ in sub])
        gv = np.array([float(g[gid]) for _, g in sub])
        sec = np.array([float(g["A_mean" if gid.startswith("A") else "B_mean"]) for _, g in sub])
        rho = stats.spearmanr(hv, sec).statistic if len(sub) >= 3 and hv.std() and sec.std() else math.nan
        rows.append({"human score": col, "vs grader": gid, "n": len(sub), "human mean": f"{hv.mean():.2f}",
                     "grader mean": f"{gv.mean():.2f}", "mean |human − grader|": f"{np.abs(hv - gv).mean():.2f}",
                     "within 1 point": f"{np.mean(np.abs(hv - gv) <= 1):.0%}",
                     "Spearman vs grader section mean": "–" if math.isnan(rho) else f"{rho:.2f}"})
    if rows:
        out += [md_table(pd.DataFrame(rows)), ""]

    # Handoff score against each grader B item and the side-by-side win rate.
    sub = [(float(h["handoff_score"]), g) for h, g in pairs if has(h, "handoff_score")]
    if len(sub) >= 3:
        hv = np.array([x for x, _ in sub])
        irows = []
        pw = load_pairwise()
        wins = run_win_rates(pw) if pw is not None and len(pw) else pd.Series(dtype=float)
        measures = [(k, ITEM_NAMES[k], [float(g[k]) for _, g in sub]) for k in B_ITEMS]
        measures.append(("B_mean", "Section B mean", [float(g["B_mean"]) for _, g in sub]))
        if all(g.run_id in wins for _, g in sub):
            measures.append(("pairwise", "Side-by-side win rate", [wins[g.run_id] for _, g in sub]))
        for k, name, gv in measures:
            gv = np.array(gv)
            rho = stats.spearmanr(hv, gv).statistic if gv.std() and hv.std() else math.nan
            irows.append({"grader measure": f"{k} {name}", "grader values": " ".join(f"{v:g}" for v in gv.round(2)),
                          "mean − human": f"{(gv - hv).mean():+.2f}" if k != "pairwise" else "–",
                          "Spearman with human": "no spread" if math.isnan(rho) else f"{rho:.2f}"})
        out += [f"**Handoff score item by item** (n = {len(sub)}; human scores: "
                f"{' '.join(f'{v:g}' for v in hv)}):", "", md_table(pd.DataFrame(irows)), ""]

    # Best/not-best picks: does the grader rank the human's picks higher?
    picks = [(str(h["picked_best"]).strip().lower() == "yes", g) for h, g in pairs if has(h, "picked_best")]
    yes, no = [g for p, g in picks if p], [g for p, g in picks if not p]
    if yes and no:
        prow = []
        for col in ("B_mean", "A_mean", "B12"):
            conc = np.mean([1.0 if a[col] > b[col] else 0.5 if a[col] == b[col] else 0.0 for a in yes for b in no])
            prow.append({"grader measure": col, "mean for human's picks": f"{np.mean([g[col] for g in yes]):.2f}",
                         "mean for the rest": f"{np.mean([g[col] for g in no]):.2f}",
                         "concordance": f"{conc:.2f}"})
        out += [f"**Best/not-best picks:** {len(yes)} picked of {len(picks)}. Concordance = share of "
                "(picked, not picked) pairs the grader ranks the same way (ties count half; 0.5 = chance).", "",
                md_table(pd.DataFrame(prow)), ""]

    # Stated pairwise preferences (human_scoring/batch_*/human_pairwise.csv: better, worse).
    prow = []
    pw_all = load_pairwise()
    for f in sorted(HUMAN_DIR.glob("batch_*/human_pairwise.csv")):
        key = {r["sample_id"]: r["run_id"] for r in csv.DictReader(open(f.parent / "_key.csv", encoding="utf-8"))}
        for r in csv.DictReader(open(f, encoding="utf-8")):
            a, b = key[r["better"]], key[r["worse"]]
            ga, gb = scores[scores.run_id == a], scores[scores.run_id == b]
            if not (len(ga) and len(gb)):
                continue
            judged = pw_all[((pw_all.run_first == a) & (pw_all.run_second == b)) |
                            ((pw_all.run_first == b) & (pw_all.run_second == a))] if pw_all is not None else []
            prow.append({"human: better > worse": f"{r['better']} > {r['worse']}",
                         "rubric B_mean agrees": "yes" if ga.B_mean.iloc[0] > gb.B_mean.iloc[0] else
                         "tie" if ga.B_mean.iloc[0] == gb.B_mean.iloc[0] else "no",
                         "side-by-side judge agrees": f"{int((judged.winner_run == a).sum())}/{len(judged)}"
                         if len(judged) else "–"})
    if prow:
        out += ["**Stated pairwise preferences vs. the grader:**", "", md_table(pd.DataFrame(prow)), ""]

    gc = [(str(h["guardrail_concern"]).strip().lower() == "yes",
           any(g[f"flag_{k}"] == "yes" for k in GUARDRAILS)) for h, g in pairs if has(h, "guardrail_concern")]
    if gc:
        out += [f"**Guardrail concern:** human yes in {sum(a for a, _ in gc)}/{len(gc)} cases, grader any flag in "
                f"{sum(b for _, b in gc)}/{len(gc)}; agree in {np.mean([a == b for a, b in gc]):.0%}.", ""]
    return out


def section_burden(scores: pd.DataFrame, runs: pd.DataFrame, prompts: list[str]) -> list[str]:
    out = ["## Burden on the owner", "",
           "A3 (efficient sequence) and A7 (avoids burdensome probing) from the grader, plus counts from the runs. "
           "Assistant words per turn is how much the owner has to read each time.", ""]
    rows = []
    for p in prompts:
        s, r = scores[scores.prompt == p], runs[runs.prompt == p]
        rows.append({"prompt": p, "runs": len(r), "A3": mean_sd(s.A3) if len(s) else "–",
                     "A7": mean_sd(s.A7) if len(s) else "–", "exchanges": mean_sd(r.n_exchanges),
                     "owner words (total)": f"{r.owner_word_count.mean():.0f}",
                     "assistant words per turn": f"{(r.assistant_words / r.n_exchanges.clip(lower=1)).mean():.0f}"})
    out += [md_table(pd.DataFrame(rows)), ""]
    a = prompts[0]
    for b in prompts[1:]:
        parts = []
        for col, src in (("A7", scores), ("A3", scores), ("n_exchanges", runs), ("owner_word_count", runs)):
            d = paired_diffs(src, col, a, b)
            if len(d):
                parts.append(f"{col} {fmt(*t_ci(d), d=1 if col in ('n_exchanges', 'owner_word_count') else 2)}")
        if parts:
            out += [f"**Paired by persona ({b} − {a}):** " + "; ".join(parts) + ".", ""]
    return out


def owner_turn_rows(full_runs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in full_runs:
        for t in r["turns"]:
            if t["speaker"] != "owner" or t.get("fixed_opening"):
                continue
            rz = t.get("realism") or {}
            feats = rz.get("features", {})
            rows.append({"run_id": r["run_id"], "prompt": r["prompt"], "persona": r["persona"],
                         "owner_turn": t.get("owner_turn"), "words": t["word_count"],
                         "target": rz.get("word_target"), "multi_part": feats.get("multi_part"),
                         "sensitive": feats.get("sensitive"), "behaviors": rz.get("behaviors", []),
                         "regen": t.get("regenerated_for", [])})
    return pd.DataFrame(rows)


def section_realism(full_runs: list[dict], prompts: list[str]) -> list[str]:
    ot = owner_turn_rows(full_runs)
    out = ["## Owner realism report", ""]
    if not len(ot) or ot.target.isna().all():
        return out + ["_No runs with the realism owner yet._", ""]
    ot["over"] = ot.words > ot.target
    ot["partial"] = ot.behaviors.apply(lambda b: "partial" in b)
    ot["unsure"] = ot.behaviors.apply(lambda b: "unsure" in b or "skip" in b)  # v2 logged all non-answers as "skip"
    ot["decline"] = ot.behaviors.apply(lambda b: "decline" in b)
    ot["regen_len"] = ot.regen.apply(lambda b: "length" in b)
    ot["regen_leak"] = ot.regen.apply(lambda b: "leak" in b)
    rows = []
    for (pe, p), g in ot.groupby(["persona", "prompt"]):
        mp = g[g.multi_part == True]  # noqa: E712
        rows.append({"persona": pe, "prompt": p, "owner turns": len(g),
                     "words/turn (median)": f"{g.words.median():.0f}", "target (median)": f"{g.target.median():.0f}",
                     "over target": f"{g.over.mean():.0%}", "multi-part questions": f"{len(mp) / len(g):.0%}",
                     "partial answers (of multi-part)": f"{mp.partial.mean():.0%}" if len(mp) else "–",
                     "not sure": f"{g.unsure.mean():.0%}", "prefer not to say": f"{g.decline.mean():.0%}",
                     "regenerated (length / leak)":
                     f"{int(g.regen_len.sum())} / {int(g.regen_leak.sum())}"})
    out += ["Directives come from the harness (prompt-blind features + seeded draws); see each run's JSON "
            "under `turns[].realism`. Multi-part share is a property of the prompt's questions; the partial rate "
            "applies the persona's rate to those.", "", md_table(pd.DataFrame(rows)), ""]
    ot["bin"] = pd.cut(ot.owner_turn, [0, 8, 16, 24, 999], labels=["1–8", "9–16", "17–24", "25+"])
    fat = ot.groupby(["prompt", "bin"], observed=True).agg(words=("words", "mean"), turns=("words", "size"))
    frows = []
    for p in prompts:
        r = {"prompt": p}
        for bname in ["1–8", "9–16", "17–24", "25+"]:
            r[f"owner turns {bname}"] = (f"{fat.loc[(p, bname), 'words']:.0f} (n={fat.loc[(p, bname), 'turns']})"
                                         if (p, bname) in fat.index else "–")
        frows.append(r)
    out += ["**Fatigue:** mean owner words per turn by how far into the conversation the turn falls.", "",
            md_table(pd.DataFrame(frows)), ""]
    return out


def section_stress(prompts: list[str]) -> list[str]:
    path = SCORES_DIR / "stress_tests.csv"
    out = ["## Guardrail stress tests (separate from the main scores)", ""]
    if not path.exists():
        return out + ["_No stress-test runs scored yet._", ""]
    st = pd.read_csv(path, dtype=str, keep_default_na=False)
    rows = []
    for (sc, bid), g in st.groupby(["scenario", "behavior_id"], sort=False):
        r = {"scenario": sc, "behavior": f"{bid}: {g.behavior.iloc[0][:80]}"}
        for p in prompts:
            gp = g[g.prompt == p]
            n_app = (gp.result != "not_applicable").sum()
            r[p] = f"{(gp.result == 'pass').sum()}/{n_app} pass" + (
                f" ({(gp.result == 'not_applicable').sum()} n/a)" if (gp.result == "not_applicable").any() else "")
        rows.append(r)
    fails = st[st.result == "fail"]
    out += [md_table(pd.DataFrame(rows)), "", f"Failures with quotes: {len(fails)} (in `scores/stress_tests.csv` "
            "and `scores/stress_full/`).", ""]
    for _, f in fails.head(12).iterrows():
        out.append(f"- **{f.scenario} {f.behavior_id}** ({f.prompt}, run {f.run_n}): \"{f.quote[:220]}\"")
    return out + [""]


def section_demographics(prompts: list[str], run_ids: set[str]) -> list[str]:
    path = DATA_DIR / "demographic_checks.csv"
    out = ["## Demographic attributes in handoffs", ""]
    if not path.exists():
        return out + ["_No demographic checks yet._", ""]
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    d = d[d.run_id.isin(run_ids)]
    found = d[~d.category.isin(["none", "no_handoff"])]
    rows = []
    for p in prompts:
        dp, fp = d[d.prompt == p], found[found.prompt == p]
        rows.append({"prompt": p, "runs checked": dp.run_id.nunique(),
                     "runs with any flag": f"{fp.run_id.nunique()}/{dp.run_id.nunique()}",
                     "by category": "; ".join(f"{k} {v}" for k, v in fp.category.value_counts().items()) or "none"})
    out += [md_table(pd.DataFrame(rows)), ""]
    for _, f in found.head(10).iterrows():
        out.append(f"- {f.category} ({f.persona}, {f.prompt}): \"{f.quote[:160]}\"")
    return out + [""]


def section_owner_versions(all_runs: pd.DataFrame, scores: pd.DataFrame | None, personas: list[str],
                           prompts: list[str]) -> list[str]:
    sub = all_runs[all_runs.persona.isin(personas) & ~all_runs.stress_test]
    if sub.owner_version.nunique() < 2:
        return []
    out = ["## Old vs. new owner (original personas)", "",
           "v1 = pilot owner (no friction); v2 = realism owner. Pilot runs were re-graded with rubric v2, "
           "so scores are comparable.", ""]
    s = sub.merge(scores[["run_id", "A_mean", "B_mean", "A7"]], on="run_id", how="left") if scores is not None else sub
    rows = []
    for (v, p), g in s.groupby(["owner_version", "prompt"]):
        rows.append({"owner": v, "prompt": p, "runs": len(g), "exchanges": f"{g.n_exchanges.mean():.1f}",
                     "owner words/turn": f"{(g.owner_word_count / g.n_exchanges.clip(lower=1)).mean():.0f}",
                     "Section A": f"{g.A_mean.mean():.2f}" if "A_mean" in g else "–",
                     "Section B": f"{g.B_mean.mean():.2f}" if "B_mean" in g else "–",
                     "A7": f"{g.A7.mean():.2f}" if "A7" in g else "–"})
    return out + [md_table(pd.DataFrame(rows)), ""]


def main() -> None:
    cfg = load_config()
    version = cfg["conversation"].get("owner_sim_version", "v1")
    full = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RUNS_DIR.glob("*.json"))]
    if not full:
        print("no runs yet")
        return
    all_runs = pd.DataFrame([{
        "run_id": r["run_id"], "prompt": r["prompt"], "persona": r["persona"], "run_n": r["run_n"],
        "owner_version": r.get("owner_sim_version", "v1"), "stress_test": bool(r.get("stress_test")),
        "n_exchanges": r["n_exchanges"], "owner_word_count": r["owner_word_count"],
        "assistant_words": r["assistant_word_count_excl_handoff"], "stop_reason": r["stop_reason"],
        "cost": r["usage"]["total_cost_usd"]} for r in full])
    # Main analysis: the current owner version if it has runs, else the newest one present.
    if not ((all_runs.owner_version == version) & ~all_runs.stress_test).any():
        version = sorted(all_runs.owner_version.unique())[-1]
    runs = all_runs[(all_runs.owner_version == version) & ~all_runs.stress_test].reset_index(drop=True)
    ids = set(runs.run_id)
    full_main = [r for r in full if r["run_id"] in ids]
    registry = list(load_yaml(PROMPTS_DIR / "registry.yaml"))  # control first: differences read "later − control"
    prompts = [p for p in registry if p in set(runs.prompt)] + sorted(set(runs.prompt) - set(registry))

    out = ["# Synthetic testing summary", "",
           f"Main analysis: owner {version} · {len(runs)} runs · prompts: {', '.join(prompts)} · "
           f"personas: {runs.persona.nunique()} · total API spend so far: ${ledger_total():.2f}", "",
           "Small n: treat every interval below as a description of these runs, not evidence of a general difference. "
           "Stress-test runs are reported separately and never enter the main averages.", ""]
    scores_path = SCORES_DIR / "scores.csv"
    all_scores = pd.read_csv(scores_path, dtype={"run_n": int}) if scores_path.exists() else None
    scores = all_scores[all_scores.run_id.isin(ids)] if all_scores is not None else None
    if scores is not None and len(scores):
        out += section_scores(scores, prompts)
        pw = load_pairwise()
        if pw is not None and len(pw):
            pw = pw[pw.run_first.isin(ids) & pw.run_second.isin(ids)]
            if len(pw):
                out += section_pairwise(pw, prompts)
        out += section_flags(scores, prompts) + section_burden(scores, runs, prompts)
    else:
        out += ["_No grader scores yet._", ""]
    out += section_length(runs, prompts) + section_realism(full_main, prompts)
    long_path = DATA_DIR / "structured_records_long.csv"
    if long_path.exists():
        long = pd.read_csv(long_path, dtype=str, keep_default_na=False)
        out += section_records(long[long.run_id.isin(ids)], prompts)
    stress_ids = set(all_runs[all_runs.stress_test].run_id)
    out += section_demographics(prompts, ids | stress_ids) + section_stress(prompts)
    if scores is not None and len(scores):
        out += section_sim(scores)
    out += section_owner_versions(all_runs, all_scores, cfg["pilot"]["personas"], prompts)
    if all_scores is not None and len(all_scores):
        out += section_agreement(all_scores)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
