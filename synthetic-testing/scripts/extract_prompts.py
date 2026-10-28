"""Copy each registered prompt version into prompts/<name>.txt as plain text, and
parse its STRUCTURED INTAKE RECORD TEMPLATE into prompts/<name>.fields.yaml.

.docx sources go through pandoc (bundled via pypandoc_binary); .txt/.md are copied.
Originals are only read. Re-run whenever prompts/registry.yaml changes.
"""
from __future__ import annotations

import re
import sys

import pypandoc
import yaml

from common import PROJECT_ROOT, PROMPTS_DIR, load_yaml, sha256_text

TEMPLATE_HEADING = "STRUCTURED INTAKE RECORD TEMPLATE"
# Template lines that describe the record rather than hold owner information.
META_CATEGORIES = {"Capture Status"}


def parse_template(text: str) -> list[dict]:
    """Return [{category, fields: [...]}, ...] from the record template block."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == TEMPLATE_HEADING), None)
    if start is None:
        return []
    cats = []
    for line in lines[start + 1:]:
        if line.strip().startswith("Begin now"):
            break
        m = re.match(r"^([A-Z][A-Za-z &/,-]+):\s*(.+)$", line.strip())
        if not m:
            continue
        cat, rest = m.group(1).strip(), m.group(2)
        if cat in META_CATEGORIES:
            continue
        fields = [f.strip() for f in rest.split(";") if f.strip()]
        cats.append({"category": cat, "fields": fields})
    return cats


def to_text(src) -> str:
    if src.suffix.lower() == ".docx":
        return pypandoc.convert_file(str(src), "plain", extra_args=["--wrap=none"])
    return src.read_text(encoding="utf-8")


def main() -> None:
    registry = load_yaml(PROMPTS_DIR / "registry.yaml")
    only = set(sys.argv[1:])
    for name, entry in registry.items():
        if only and name not in only:
            continue
        src = PROJECT_ROOT / entry["source"]
        text = to_text(src).replace("\r\n", "\n")
        marker = entry.get("marker")
        if marker:
            lines = text.split("\n")
            idx = next((i for i, l in enumerate(lines) if marker in l), None)
            if idx is None:
                sys.exit(f"{name}: marker '{marker}' not found in {src}")
            text = "\n".join(lines[idx + 1:])
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        out = PROMPTS_DIR / f"{name}.txt"
        out.write_text(text, encoding="utf-8")
        template = parse_template(text)
        n_fields = sum(len(c["fields"]) for c in template)
        with open(PROMPTS_DIR / f"{name}.fields.yaml", "w", encoding="utf-8") as f:
            f.write(f"# Parsed from the '{TEMPLATE_HEADING}' block of {name}.txt. Do not edit; re-run extract_prompts.py.\n")
            yaml.safe_dump(template, f, sort_keys=False, allow_unicode=True)
        print(f"{name}: {len(text.split())} words, sha256 {sha256_text(text)[:12]}, "
              f"{len(template)} record categories / {n_fields} fields")
        if not template:
            print(f"  WARNING: no '{TEMPLATE_HEADING}' block found in {name}")


if __name__ == "__main__":
    main()
