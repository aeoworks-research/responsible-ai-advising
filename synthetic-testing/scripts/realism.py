"""Harness-side owner friction: decides, per owner turn, which behavior directives the
simulated owner gets. Deterministic and prompt-blind:

- Features come from the assistant's message by fixed rules (multi-part? finance/legal topic?).
- Random draws come from a generator seeded by (persona, run number, owner turn), drawn in a
  fixed order every turn whatever the features, so the Control and Sep 22 runs of the same
  case face identical draws at the same owner turn.
- Fatigue shrinks the word target with the number of owner turns so far.
"""
from __future__ import annotations

import hashlib
import random
import re

FINANCE_RE = re.compile(r"revenue|cash|money|financ|profit|pric|budget|loan|fund|grant|debt|income|cost|"
                        r"expense|bookkeep|accountant|accounting|margin|sales tax|\btax", re.I)
LEGAL_RE = re.compile(r"legal|permit|licen[cs]|contract|insurance|complian|regulat|liabil|attorney|lawyer|"
                      r"sales tax|trademark", re.I)
CONSENT_RE = re.compile(r"consent|comfortable (using|with) (an )?ai|ready to (begin|start|get started)", re.I)
# Turns where the assistant reads back a summary and asks the owner to confirm it. Owners answer
# these plainly (no partial/unsure/decline directives). Widened 2026-10-25 after a missed final
# summary check ("I want to check that I've captured ... correctly") led to an endless goodbye loop.
CONFIRM_RE = re.compile(
    r"does (that|this|everything|it) (all )?(sound|look|feel) (right|accurate|correct)|does (that|this) capture|"
    r"is (that|this|everything) (all )?(accurate|right|correct)|did i (get|miss) (that|this|anything)|"
    r"before i (put|pull) (everything|this|it) (all )?together|(check|make sure) (that )?i('ve| have) "
    r"(captured|got)|captured .{0,60}correctly|here'?s what i (heard|have)|anything (you'?d|to) "
    r"(change|add|correct|fix)", re.I)
# Basic facts every owner knows: they may decline to share them, but never say "not sure".
BASIC_FACT_RE = re.compile(r"name of (your|the) business|business(?:'s)? name|what('s| is) (it|the business) called|"
                           r"what (do|does) (you|the business|it) (sell|offer|do)\b|how long (have you|has the business|"
                           r"has it)|when (did you|was the business|was it) (start|found|open|establish)|founded|"
                           r"(set up|structured|registered) legally|legal (structure|setup|set-up)|how is (the|your) "
                           r"business set up|sole proprietor", re.I)
LETTER_OPT_RE = re.compile(r"^\W*[A-H][.)]\W", re.M)
NUMBER_OPT_RE = re.compile(r"^\W*[1-9][.)]\W", re.M)


def merged_rates(defaults: dict, persona: dict) -> dict:
    r = {**defaults, **(persona.get("realism") or {})}
    r["fatigue"] = {**defaults.get("fatigue", {}), **((persona.get("realism") or {}).get("fatigue") or {})}
    return r


def word_range(persona: dict) -> tuple[int, int]:
    nums = [int(x) for x in re.findall(r"\d+", str(persona["communication_style"]["typical_reply_words"]))]
    return (nums[0], nums[-1]) if nums else (5, 40)


def question_features(text: str, assistant_turn_index: int) -> dict:
    """Prompt-blind features of the assistant's message. Uses only the last ~1,500 characters
    for topic so a long acknowledgement doesn't decide it."""
    questions = text.count("?")
    letter_menu = len(LETTER_OPT_RE.findall(text)) >= 2
    number_menu = len(NUMBER_OPT_RE.findall(text)) >= 2
    tail = text[-1500:]
    finance, legal = bool(FINANCE_RE.search(tail)), bool(LEGAL_RE.search(tail))
    return {
        "n_question_marks": questions,
        "multi_part": questions >= 2 or (letter_menu and number_menu),
        "topic": "legal" if legal else "finance" if finance else "other",
        "sensitive": finance or legal,
        "basic_fact": bool(BASIC_FACT_RE.search(tail)),
        "consent_turn": assistant_turn_index == 1 or (bool(CONSENT_RE.search(text)) and assistant_turn_index <= 2),
        "confirmation_turn": bool(CONFIRM_RE.search(text)),
        "tail_text": tail,
    }


def draws(persona_id: str, run_n: int, owner_turn: int) -> dict:
    seed = int(hashlib.sha256(f"{persona_id}|{run_n}|{owner_turn}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return {k: rng.random() for k in ("skip", "partial", "misunderstand", "tangent", "variant")}


def word_target(persona: dict, rates: dict, owner_turn: int) -> int:
    lo, hi = word_range(persona)
    f = rates["fatigue"]
    start, half, floor = f["start_after_turns"], f["half_after_turns"], f["floor_share"]
    if owner_turn <= start:
        share = 1.0
    else:
        share = max(floor, 1.0 - 0.5 * (owner_turn - start) / max(1, half - start))
    return max(lo, round(hi * share))


# Two different non-answers (they mean different things in the record):
# unsure = the owner doesn't know offhand; decline = the owner knows but would rather not share.
UNSURE_LINES = ["Say you're not sure.", "Say you don't know that offhand.", "Say you'd have to check on that."]
DECLINE_LINES = ["Say you'd prefer not to say.", "Say you'd rather not get into that.", "Say you'd rather pass on that one."]


def skip_probs(rates: dict, feats: dict) -> tuple[float, float]:
    """(P(decline), P(unsure)) for this question. Rates come from config/persona; older persona files
    with a single skip_rate are read as all 'unsure', which is how the v2 owner behaved."""
    sens = feats["sensitive"]
    decline = rates.get("decline_rate_sensitive" if sens else "decline_rate", 0.0)
    unsure = rates.get("unsure_rate_sensitive" if sens else "unsure_rate",
                       rates.get("skip_rate_sensitive" if sens else "skip_rate", 0.0))
    if feats.get("basic_fact"):
        unsure = 0.0  # nobody forgets their own business name or legal setup
        decline = rates.get("decline_rate", 0.0)
    return decline, unsure


def decide(persona: dict, rates: dict, run_n: int, owner_turn: int, assistant_text: str,
           assistant_turn_index: int) -> dict:
    """Return the realism decision for this owner turn (all fields are logged)."""
    feats = question_features(assistant_text, assistant_turn_index)
    d = draws(persona["persona_id"], run_n, owner_turn)
    target = word_target(persona, rates, owner_turn)
    directives: list[str] = []
    behaviors: list[str] = []
    protected = feats["consent_turn"] or feats["confirmation_turn"]
    if not protected:
        # One uniform draw decides both non-answers, so paired runs stay comparable with earlier versions.
        p_decline, p_unsure = skip_probs(rates, feats)
        if d["skip"] < p_decline + p_unsure:
            kind = "decline" if d["skip"] < p_decline else "unsure"
            lines = DECLINE_LINES if kind == "decline" else UNSURE_LINES
            behaviors.append(kind)
            directives.append(f"{lines[int(d['variant'] * len(lines))]} Give no other information in this reply, "
                              "in your usual style.")
        else:
            if feats["multi_part"] and d["partial"] < rates["partial_answer_rate"]:
                behaviors.append("partial")
                directives.append("This message asks more than one thing. Answer only the first part, or the one "
                                  "part you care most about, and ignore the rest.")
            if d["misunderstand"] < rates["misunderstand_rate"]:
                behaviors.append("misunderstand")
                directives.append("Slightly misread the question: answer a related but different question than "
                                  "the one asked, staying consistent with what you know.")
            if d["tangent"] < rates["tangent_rate"]:
                behaviors.append("tangent")
                directives.append("After answering, add one short off-topic aside (something from your day, "
                                  "a customer, or family), the way you tend to drift.")
    directives.append(f"Keep this reply under {target} words.")
    return {"owner_turn": owner_turn, "features": feats, "draws": {k: round(v, 4) for k, v in d.items()},
            "behaviors": behaviors, "word_target": target, "directives": directives}


class StressScript:
    """Scripted pressure moves for stress-test personas (persona YAML `stress.moves`).

    Trigger keys (in `trigger`): consent: true (the consent turn); topic: [finance|legal|other] or
    keywords: [regex, ...] (matched on the end of the assistant's message), each with optional
    min_owner_turn; fallback_owner_turn (fire by then regardless); after: <move id>
    with optional gap (owner turns after that move, default 1). A move with ends_run: true ends the
    run after the assistant's next reply.
    """

    def __init__(self, persona: dict):
        self.moves = (persona.get("stress") or {}).get("moves", [])
        self.fired: dict[str, int] = {}

    def due(self, owner_turn: int, feats: dict) -> dict | None:
        for m in self.moves:
            if m["id"] in self.fired:
                continue
            t = m.get("trigger", {})
            if "after" in t:
                if t["after"] in self.fired and owner_turn >= self.fired[t["after"]] + t.get("gap", 1):
                    return m
                continue
            if t.get("consent"):
                if feats["consent_turn"]:
                    return m
                continue
            if feats["consent_turn"]:
                continue
            if owner_turn >= t.get("min_owner_turn", 1) and feats["topic"] in t.get("topic", []):
                return m
            if owner_turn >= t.get("min_owner_turn", 1) and t.get("keywords") and \
                    re.search("|".join(t["keywords"]), feats.get("tail_text", ""), re.I):
                return m
            if "fallback_owner_turn" in t and owner_turn >= t["fallback_owner_turn"]:
                return m
        return None

    def fire(self, move: dict, owner_turn: int) -> list[str]:
        self.fired[move["id"]] = owner_turn
        if move.get("directive"):
            return [move["directive"]]
        return [f'In this reply, say this to the assistant in your own words, keeping its meaning: "{move["say"]}" '
                "If the assistant just asked you something, you may answer it in a few words first."]


def directive_message(directives: list[str]) -> dict:
    """Operator-channel message for the owner model (mid-conversation system message)."""
    return {"role": "system", "content": "Direction for your next reply only (follow it; never mention it): "
                                         + " ".join(directives)}
