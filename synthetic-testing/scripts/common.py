"""Shared helpers: paths, config, API client, model calls, cost accounting."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import yaml

ST_ROOT = Path(__file__).resolve().parent.parent          # synthetic_testing/
PROJECT_ROOT = ST_ROOT.parent                             # repository root
PROMPTS_DIR = ST_ROOT / "prompts"
PERSONAS_DIR = ST_ROOT / "personas"
RUNS_DIR = ST_ROOT / "runs"
DATA_DIR = ST_ROOT / "data"
SCORES_DIR = ST_ROOT / "scores"
RUBRIC_DIR = ST_ROOT / "rubric"
HUMAN_DIR = ST_ROOT / "human_scoring"
TEMPLATES_DIR = ST_ROOT / "templates"

KEY_ENV = "ANTHROPIC_API_KEY"

# Fields that must never be read or sent anywhere (TIN/EIN/SSN-like).
FORBIDDEN_FIELD_RE = re.compile(r"\b(tin|ein|ssn|itin|fein|tax\s*id|taxpayer|social\s*security|employer\s*id)", re.I)


def load_config() -> dict:
    with open(ST_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prompt(name: str) -> tuple[str, str]:
    """Return (prompt text, sha256) for a registered prompt."""
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        sys.exit(f"Prompt '{name}' not found at {path}. Run scripts/extract_prompts.py first.")
    text = path.read_text(encoding="utf-8")
    return text, sha256_text(text)


def load_persona(persona_id: str) -> dict:
    """Load a persona. A persona with `base_persona` inherits that persona's fields; its own
    fields win, and its fact_sheet modules are appended to the base's."""
    path = PERSONAS_DIR / f"{persona_id}.yaml"
    if not path.exists():
        sys.exit(f"Persona '{persona_id}' not found at {path}.")
    p = load_yaml(path)
    if p.get("base_persona"):
        base = load_persona(p["base_persona"])
        facts = {k: list(v) for k, v in base["fact_sheet"].items()}
        for k, v in (p.get("fact_sheet") or {}).items():
            facts.setdefault(k, []).extend(v)
        p = {**base, **p, "fact_sheet": facts}
    return p


def persona_hash(persona: dict) -> str:
    """Hash of the fully resolved persona (includes anything inherited from a base persona)."""
    return sha256_text(json.dumps(persona, sort_keys=True, ensure_ascii=False))


def fact_sheet_text(persona: dict, with_tags: bool = False) -> str:
    """Render a persona's fact sheet as plain text.

    The owner sim gets it without tags; with_tags=True marks invented facts for review.
    """
    lines = []
    for module, facts in persona["fact_sheet"].items():
        lines.append(f"## {module}")
        for fact in facts:
            tag = " [invented]" if with_tags and fact.get("invented") else ""
            lines.append(f"- {fact['fact']}{tag}")
        lines.append("")
    return "\n".join(lines).strip()


def load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- API client

_client = None


def get_client():
    global _client
    if _client is None:
        import anthropic
        key = os.environ.get(KEY_ENV)
        if not key:
            sys.exit(
                f"{KEY_ENV} is not set. Set it as a user environment variable and restart "
                "the terminal. The key is read only from this variable and never written to a file."
            )
        _client = anthropic.Anthropic(api_key=key, max_retries=4)
    return _client


def role_settings(cfg: dict, role: str) -> dict:
    return cfg["roles"][role]


def call_model(cfg: dict, role: str, system: str, messages: list, *, max_tokens: int | None = None,
               output_format: dict | None = None):
    """One Messages API call for a configured role.

    System prompt is cached (block-level breakpoint); top-level automatic caching
    also caches the growing conversation prefix. No sampling parameters are sent.
    Returns (response, text, usage_dict).
    """
    s = role_settings(cfg, role)
    output_config = {"effort": s["effort"]}
    if output_format:
        output_config["format"] = output_format
    kwargs = dict(
        model=s["model"],
        max_tokens=max_tokens or s.get("max_tokens", 16000),
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config=output_config,
        cache_control={"type": "ephemeral"},
    )
    client = get_client()
    if kwargs["max_tokens"] > 16000:
        with client.messages.stream(**kwargs) as stream:
            resp = stream.get_final_message()
    else:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
        raise RuntimeError(f"{role} call refused (category={cat}); request id {resp._request_id}")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return resp, text, usage_dict(resp, s["model"], cfg)


def usage_dict(resp, model: str, cfg: dict) -> dict:
    u = resp.usage
    d = {
        "model": model,
        "input_tokens": u.input_tokens or 0,
        "output_tokens": u.output_tokens or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }
    d["cost_usd"] = cost_usd(d, cfg)
    return d


def cost_usd(u: dict, cfg: dict) -> float:
    p = cfg["pricing_usd_per_mtok"][u["model"]]
    return (
        u["input_tokens"] * p["input"]
        + u["cache_creation_input_tokens"] * p["cache_write"]
        + u["cache_read_input_tokens"] * p["cache_read"]
        + u["output_tokens"] * p["output"]
    ) / 1_000_000


def sum_usage(usages: list[dict]) -> dict:
    """Aggregate a list of usage dicts by model and in total."""
    by_model: dict[str, dict] = {}
    for u in usages:
        m = by_model.setdefault(u["model"], {k: 0 for k in
                                             ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                              "cache_read_input_tokens", "cost_usd", "calls")})
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "cost_usd"):
            m[k] += u[k]
        m["calls"] += 1
    total = round(sum(m["cost_usd"] for m in by_model.values()), 4)
    for m in by_model.values():
        m["cost_usd"] = round(m["cost_usd"], 4)
    return {"by_model": by_model, "total_cost_usd": total}


# ---------------------------------------------------------------- spend ledger

LEDGER = DATA_DIR / "spend_ledger.jsonl"


def ledger_total() -> float:
    if not LEDGER.exists():
        return 0.0
    total = 0.0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += json.loads(line)["cost_usd"]
    return total


_ledger_lock = threading.Lock()


def ledger_add(step: str, run_id: str, cost: float) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with _ledger_lock, open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step,
                            "run_id": run_id, "cost_usd": round(cost, 5)}) + "\n")


def check_budget(cfg: dict) -> None:
    spent = ledger_total()
    if spent >= cfg["budget_usd"]:
        sys.exit(f"Budget cap reached: ${spent:.2f} spent of ${cfg['budget_usd']:.2f} "
                 "(see data/spend_ledger.jsonl; raise budget_usd in config.yaml to continue).")


def parse_json_block(text: str):
    """Parse JSON from a model reply, tolerating ```json fences."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = m.group(1) if m else text
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=0)
    return json.loads(body[start:])
