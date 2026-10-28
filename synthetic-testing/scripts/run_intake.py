"""Run simulated intake conversations (prompt version x persona x repetition).

The intake assistant gets the prompt version as its system prompt. The simulated
owner gets only its persona and style. Each run writes runs/<run_id>.md (transcript +
advisor handoff), runs/<run_id>.json (run details + per-turn data), and
runs/<run_id>.handoff.html when the handoff is HTML.

With owner_sim_version other than v1 (config.yaml), each owner turn gets harness-side
behavior directives from scripts/realism.py (logged per turn under "realism"), and stress
personas play their scripted pressure moves.

Usage:
  python scripts/run_intake.py --pilot [--workers 3]
  python scripts/run_intake.py --grid example_grid [--no-stress | --stress-only] [--date YYYY-MM-DD]
  python scripts/run_intake.py --prompt consolidated_sep22 --persona s1_wants_lender --n 1
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common import (RUNS_DIR, call_model, check_budget, fact_sheet_text, ledger_add, ledger_total,
                    load_config, load_persona, load_prompt, load_template, persona_hash, sha256_text, sum_usage)
from realism import StressScript, decide, directive_message, merged_rates, word_range

HTML_RE = re.compile(r"<!doctype html|<html|<table", re.I)
# Signs the simulated owner leaked its notes or reasoning instead of just replying.
OWNER_LEAK_RE = re.compile(r"^\s*[A-Z][\w/ ]{2,30}:\s|\bbest fit\b|\bpick [A-H]\b\.?|\bfact sheet\b|\bpersona\b",
                           re.I | re.M)
TEMPLATE_FILES = ["owner_system.txt", "module_tagger.txt"]
MODULES = [f"M{i}" for i in range(1, 14)]

TAG_SCHEMA = {
    "type": "object",
    "properties": {"tags": {"type": "array", "items": {
        "type": "object",
        "properties": {"turn": {"type": "integer"}, "module": {"type": "string", "enum": MODULES + ["none"]}},
        "required": ["turn", "module"], "additionalProperties": False}}},
    "required": ["tags"], "additionalProperties": False,
}


def handoff_started(text: str) -> bool:
    t = text.lower()
    return bool(HTML_RE.search(text)) or ("intake record" in t and "snapshot" in t)


def handoff_complete(text: str) -> bool:
    t = text.lower()
    has_parts = "intake record" in t and "snapshot" in t
    html_closed = "</html>" in t if "<html" in t else True
    return has_parts and html_closed


def stalled_closing(turns: list[dict], n: int = 4) -> bool:
    """True when the last n assistant turns are short and ask nothing (e.g., an endless
    goodbye exchange after the owner declines to confirm the summary)."""
    last = [t for t in turns if t["speaker"] == "assistant"][-n:]
    return len(last) == n and all("?" not in t["text"] and len(t["text"]) < 300 for t in last)


def extract_html(text: str) -> str | None:
    m = re.search(r"(<!doctype html.*?</html>|<html.*?</html>)", text, re.I | re.S)
    return m.group(1) if m else None


def owner_system_prompt(persona: dict, cfg: dict) -> str:
    style = persona["communication_style"]
    if persona.get("consents", True):
        consent_rule = "When the assistant asks for your consent to begin, agree."
    else:
        consent_rule = ("When the assistant asks for your consent, say you are not comfortable doing this "
                        "with an AI and would rather talk to your advisor directly.")
    extra = (persona.get("extra_instructions") or "").strip()
    return load_template("owner_system.txt").format(
        opening_message=cfg["conversation"]["opening_message"],
        label=persona["label"],
        facts=fact_sheet_text(persona),
        style_label=style["label"],
        style_description=" ".join(style["description"].split()),
        reply_words=style["typical_reply_words"],
        consent_rule=consent_rule,
        extra_instructions=f"- {extra}" if extra else "",
    ).strip()


def tag_modules(cfg: dict, turns: list[dict]) -> tuple[dict, dict | None]:
    """Return ({turn: module}, usage). Handoff turns are M13 without a model call."""
    lines = []
    for t in turns:
        body = "[final advisor handoff and intake record]" if t.get("is_handoff") else t["text"]
        lines.append(f"[turn {t['turn']}] {t['speaker'].upper()}: {body}")
    msg = "Conversation:\n\n" + "\n\n".join(lines)
    _, text, usage = call_model(cfg, "module_tagger", load_template("module_tagger.txt"),
                                [{"role": "user", "content": msg}],
                                output_format={"type": "json_schema", "schema": TAG_SCHEMA})
    tags = {d["turn"]: d["module"] for d in json.loads(text)["tags"]}
    for t in turns:
        if t.get("is_handoff"):
            tags[t["turn"]] = "M13"
    return tags, usage


def run_one(cfg: dict, prompt_name: str, persona_id: str, run_n: int, date: str, force: bool = False) -> str:
    run_id = f"{date}_{prompt_name}_{persona_id}_{run_n}"
    md_path, json_path = RUNS_DIR / f"{run_id}.md", RUNS_DIR / f"{run_id}.json"
    if json_path.exists() and not force:
        old = json.loads(json_path.read_text(encoding="utf-8")).get("owner_sim_version", "v1")
        if old != cfg["conversation"].get("owner_sim_version", "v1"):
            raise SystemExit(f"{run_id} exists with owner_sim_version {old}; pass a different --date.")
        print(f"skip {run_id} (exists)")
        return run_id
    check_budget(cfg)

    prompt_text, prompt_hash = load_prompt(prompt_name)
    persona = load_persona(persona_id)
    p_hash = persona_hash(persona)
    owner_sys = owner_system_prompt(persona, cfg)
    max_turns = cfg["conversation"]["max_turns"]
    opening = cfg["conversation"]["opening_message"]
    owner_version = cfg["conversation"].get("owner_sim_version", "v1")
    realism_on = owner_version != "v1"
    rates = merged_rates(cfg.get("owner_realism_defaults", {}), persona)
    regen_factor = rates.get("length_regen_factor", 2.0)
    script = StressScript(persona)
    end_after_reply = False

    started = dt.datetime.now().isoformat(timespec="seconds")
    print(f"start {run_id}")
    turns = [{"turn": 1, "speaker": "owner", "text": opening, "fixed_opening": True}]
    intake_msgs = [{"role": "user", "content": opening}]
    owner_msgs: list[dict] = []
    usages: list[dict] = []
    handoff_parts: list[str] = []
    exchanges, after_handoff, stop_reason = 0, 0, None

    try:
        while True:
            resp, text, u = call_model(cfg, "intake_assistant", prompt_text, intake_msgs)
            usages.append({**u, "role": "intake_assistant"})
            exchanges += 1
            # Full content (incl. thinking blocks) goes back unchanged; history is append-only.
            intake_msgs.append({"role": "assistant", "content": [b.to_dict() for b in resp.content]})
            is_handoff = bool(handoff_parts) or handoff_started(text)
            turns.append({"turn": len(turns) + 1, "speaker": "assistant", "text": text,
                          "api_stop_reason": resp.stop_reason, "is_handoff": is_handoff})
            if is_handoff:
                handoff_parts.append(text)
                if handoff_complete("\n\n".join(handoff_parts)) and resp.stop_reason != "max_tokens":
                    stop_reason = "handoff_complete"
                    break
                after_handoff += 1
                if after_handoff > 2:
                    stop_reason = "handoff_incomplete"
                    break
            if exchanges >= max_turns:
                stop_reason = "max_turns"
                break
            if ledger_total() + sum(x["cost_usd"] for x in usages) > cfg["budget_usd"]:
                stop_reason = "budget_cap"
                break
            if end_after_reply:
                stop_reason = "owner_stopped"
                break
            if stalled_closing(turns):
                stop_reason = "stalled_closing"
                break

            owner_turn_n = sum(1 for t in turns if t["speaker"] == "owner" and not t.get("fixed_opening")) + 1
            owner_msgs.append({"role": "user", "content": text})
            decision = None
            if realism_on:
                decision = decide(persona, rates, run_n, owner_turn_n, text, exchanges)
                move = script.due(owner_turn_n, decision["features"])
                decision["features"].pop("tail_text", None)  # used for triggers only; not logged
                if move:  # a scripted pressure move replaces the random behaviors this turn
                    decision["behaviors"] = ["stress_move"]
                    decision["stress_move"] = move["id"]
                    decision["word_target"] = max(decision["word_target"], word_range(persona)[1])
                    decision["directives"] = script.fire(move, owner_turn_n) + [
                        f"Keep this reply under {decision['word_target']} words."]
                    end_after_reply = bool(move.get("ends_run"))
                owner_msgs.append(directive_message(decision["directives"]))
            leak_regen = length_regen = False
            for attempt in (1, 2):  # regenerate once for a leak or a reply far over the word target
                oresp, otext, ou = call_model(cfg, "simulated_owner", owner_sys, owner_msgs)
                usages.append({**ou, "role": "simulated_owner"})
                too_long = bool(decision) and len(otext.split()) > regen_factor * decision["word_target"]
                leaked = bool(OWNER_LEAK_RE.search(otext))
                if attempt == 1 and (leaked or too_long):
                    leak_regen, length_regen = leaked, too_long
                    if too_long and decision:  # restate the limit more firmly for the retry
                        owner_msgs[-1] = directive_message(
                            decision["directives"] + [f"Your reply must be under {decision['word_target']} words."])
                    continue
                break
            owner_msgs.append({"role": "assistant", "content": [b.to_dict() for b in oresp.content]})
            otext = otext.strip() or "(no reply)"
            turn = {"turn": len(turns) + 1, "speaker": "owner", "text": otext, "owner_turn": owner_turn_n}
            if leak_regen or length_regen:
                turn["owner_regenerated"] = True
                turn["regenerated_for"] = [x for x, b in (("leak", leak_regen), ("length", length_regen)) if b]
                turn["owner_leak_suspected"] = bool(OWNER_LEAK_RE.search(otext))
            if decision:
                turn["realism"] = decision
                turn["over_target_after_regen"] = len(otext.split()) > decision["word_target"]
            turns.append(turn)
            intake_msgs.append({"role": "user", "content": otext})
    except Exception as e:  # keep what we have; the run is flagged
        stop_reason = f"error: {type(e).__name__}: {e}"
        print(f"  {run_id}: {stop_reason}")

    tag_usage, tags_ok = None, False
    try:
        tags, tag_usage = tag_modules(cfg, turns)
        tags_ok = True
    except Exception as e:
        print(f"  {run_id}: module tagging failed ({e}); tags left empty")
        tags = {}
    for t in turns:
        t["module"] = tags.get(t["turn"])
        t["module_inferred"] = True
        t["word_count"] = len(t["text"].split())

    all_usage = usages + ([{**tag_usage, "role": "module_tagger"}] if tag_usage else [])
    usage_summary = sum_usage(all_usage)
    by_role = {}
    for u in all_usage:
        r = by_role.setdefault(u["role"], {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
        r["calls"] += 1
        for k in ("cost_usd", "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            r[k] += u[k]
    for r in by_role.values():
        r["cost_usd"] = round(r["cost_usd"], 4)
    usage_summary["by_role"] = by_role
    ledger_add("run_intake", run_id, usage_summary["total_cost_usd"])

    import anthropic
    handoff_text = "\n\n".join(handoff_parts)
    owner_turns = [t for t in turns if t["speaker"] == "owner" and not t.get("fixed_opening")]
    record = {
        "run_id": run_id,
        "blind_id": "case_" + sha256_text(run_id)[:8],
        "prompt": prompt_name,
        "prompt_file": f"prompts/{prompt_name}.txt",
        "prompt_sha256": prompt_hash,
        "persona": persona_id,
        "persona_file_sha256": p_hash,
        "stress_test": bool(persona.get("stress_test")),
        "owner_sim_version": owner_version,
        "realism_rates": rates if realism_on else None,
        "stress_moves_fired": script.fired,
        "templates_sha256": {t: sha256_text(load_template(t)) for t in TEMPLATE_FILES},
        "realism_code_sha256": sha256_text(Path(__file__).with_name("realism.py").read_text(encoding="utf-8")),
        "owner_regenerated_turns": [t["turn"] for t in turns if t.get("owner_regenerated")],
        "run_n": run_n,
        "timestamp_start": started,
        "timestamp_end": dt.datetime.now().isoformat(timespec="seconds"),
        "stop_reason": stop_reason,
        "n_exchanges": exchanges,
        "n_turns": len(turns),
        "owner_word_count": sum(t["word_count"] for t in owner_turns),
        "assistant_word_count_excl_handoff": sum(t["word_count"] for t in turns
                                                 if t["speaker"] == "assistant" and not t.get("is_handoff")),
        "handoff_present": bool(handoff_parts),
        "handoff_complete": stop_reason == "handoff_complete",
        "settings": {
            "roles": {r: cfg["roles"][r] for r in ("intake_assistant", "simulated_owner", "module_tagger")},
            "conversation": cfg["conversation"],
            "sampling_params": "none sent (temperature/top_p/top_k unsupported)",
            "prompt_caching": "system prompt block + automatic conversation caching",
        },
        "software": {"python": platform.python_version(), "anthropic_sdk": anthropic.__version__},
        "usage": usage_summary,
        "module_tags": {"inferred": True, "model": cfg["roles"]["module_tagger"]["model"], "ok": tags_ok},
        "handoff_text": handoff_text,
        "turns": turns,
    }
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    html = extract_html(handoff_text)
    if html:
        (RUNS_DIR / f"{run_id}.handoff.html").write_text(html, encoding="utf-8")
    md_path.write_text(render_md(record), encoding="utf-8")
    print(f"done  {run_id}: {stop_reason}, {exchanges} exchanges, ${usage_summary['total_cost_usd']:.3f}")
    return run_id


def render_md(r: dict) -> str:
    s = r["settings"]["roles"]
    out = [
        f"# Run {r['run_id']}", "",
        "| Item | Value |", "|---|---|",
        f"| Prompt | `{r['prompt']}` (sha256 `{r['prompt_sha256'][:12]}`) |",
        f"| Persona | `{r['persona']}` (file sha256 `{r['persona_file_sha256'][:12]}`) |",
        f"| Run number | {r['run_n']} |",
        f"| Started / ended | {r['timestamp_start']} / {r['timestamp_end']} |",
        f"| Intake assistant | {s['intake_assistant']['model']}, effort {s['intake_assistant']['effort']} |",
        f"| Simulated owner | {s['simulated_owner']['model']}, effort {s['simulated_owner']['effort']} |",
        f"| Stop reason | {r['stop_reason']} |",
        f"| Exchanges / turns | {r['n_exchanges']} / {r['n_turns']} |",
        f"| Owner words | {r['owner_word_count']} |",
        f"| Cost | ${r['usage']['total_cost_usd']:.3f} |",
        "", "Module tags are inferred after the run by a separate model call.", "",
        "## Transcript", "",
    ]
    for t in r["turns"]:
        who = "Owner" if t["speaker"] == "owner" else "Assistant"
        mod = f" · {t['module']}" if t.get("module") else ""
        body = "_[Advisor handoff and intake record: see below]_" if t.get("is_handoff") else t["text"]
        out += [f"**{who}** (turn {t['turn']}{mod})", "", body, ""]
        if t.get("realism"):
            rz = t["realism"]
            note = ", ".join(rz["behaviors"] + ([rz["stress_move"]] if rz.get("stress_move") else [])) or "none"
            regen = f"; regenerated for {'+'.join(t['regenerated_for'])}" if t.get("owner_regenerated") else ""
            out += [f"<sub>harness: {note}; word target {rz['word_target']}{regen}</sub>", ""]
    out += ["## Advisor handoff", ""]
    if r["handoff_text"]:
        out += ["````html", r["handoff_text"], "````", ""]
    else:
        out += ["_No handoff was produced._", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="run the pilot grid from config.yaml")
    ap.add_argument("--grid", help="run a named grid from config.yaml (e.g. example_grid): prompts x personas "
                                   "(+ stress_personas) x repetitions")
    ap.add_argument("--no-stress", action="store_true", help="with --grid, leave out stress personas")
    ap.add_argument("--stress-only", action="store_true", help="with --grid, run only stress personas")
    ap.add_argument("--prompt", action="append")
    ap.add_argument("--persona", action="append")
    ap.add_argument("--n", type=int, nargs="+", help="run numbers (default 1..repetitions)")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--tag", help="label added to the date slot of the run ID (e.g. v3 -> 2026-10-25-v3_...), "
                                  "for a second set of runs on the same day")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="overwrite existing runs")
    a = ap.parse_args()
    cfg = load_config()
    RUNS_DIR.mkdir(exist_ok=True)

    grid = cfg[a.grid] if a.grid else cfg["pilot"]
    prompts = a.prompt or grid["prompts"]
    main_personas = [] if a.stress_only else grid["personas"]
    stress = [] if a.no_stress else grid.get("stress_personas", [])
    personas = a.persona or main_personas + stress
    ns = a.n or list(range(1, grid["repetitions"] + 1))
    if not (a.pilot or a.grid or a.prompt or a.persona):
        ap.error("pass --pilot, --grid NAME, or --prompt/--persona")
    # Interleave so a budget stop doesn't leave one prompt with fewer runs.
    jobs = [(p, pe, n) for n in ns for pe in personas for p in prompts]
    if a.tag:
        a.date = f"{a.date}-{a.tag}"
    print(f"{len(jobs)} runs; spent so far ${ledger_total():.2f} of ${cfg['budget_usd']:.2f}")
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_one, cfg, p, pe, n, a.date, a.force) for p, pe, n in jobs]
        for f in as_completed(futs):
            f.result()
    print(f"total spent ${ledger_total():.2f}")


if __name__ == "__main__":
    main()
