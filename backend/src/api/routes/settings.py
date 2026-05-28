"""Settings endpoints — read-only views of human-edited config.

The strategy glossary lives at docs/ai-context/strategy-glossary.md so
it stays diffable in PRs and is trivial to inject into AI prompts later.
This endpoint parses the file's section structure into JSON the
dashboard can render without pulling in a markdown library.

Parser is intentionally narrow: it knows about `##` section headers and
`- **name** — body` list items. Anything else is ignored. If the doc
shape changes, the right move is to update the parser to match the
doc — not the doc to match the parser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


# Resolved at import time from this file's location. backend/src/api/routes
# -> repo root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_GLOSSARY_PATH = _REPO_ROOT / "docs" / "ai-context" / "strategy-glossary.md"


_ITEM_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*\s*(?:—|–|-)?\s*(.*)$")


def _parse_glossary(text: str) -> dict[str, Any]:
    """Parse the glossary markdown into {sections: [{title, intro, items: [...]}]}.

    Items keep a `placeholder` flag so the UI can grey out the ones the
    user hasn't filled in yet.
    """
    sections: list[dict[str, Any]] = []
    intro_lines: list[str] = []
    current: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.rstrip()

        if line.startswith("# "):
            # Document title — skip.
            continue

        if line.startswith("## "):
            if current is not None:
                sections.append(current)
            current = {
                "title": line[3:].strip(),
                "intro": "",
                "items": [],
            }
            intro_lines = []
            continue

        match = _ITEM_RE.match(line)
        if match and current is not None:
            name = match.group(1).strip()
            body = match.group(2).strip()
            placeholder = body.lower().startswith("_fill in") or body == ""
            current["items"].append(
                {
                    "name": name,
                    "definition": body,
                    "placeholder": placeholder,
                }
            )
            continue

        if current is not None and not current["items"]:
            # Pre-list prose inside a section becomes its intro.
            if line.strip():
                intro_lines.append(line)
            elif intro_lines:
                current["intro"] = " ".join(intro_lines).strip()

    if current is not None:
        if intro_lines and not current["intro"]:
            current["intro"] = " ".join(intro_lines).strip()
        sections.append(current)

    return {"sections": sections}


@router.get("/settings/glossary")
async def get_glossary() -> dict[str, Any]:
    """Parsed view of docs/ai-context/strategy-glossary.md."""
    if not _GLOSSARY_PATH.exists():
        raise HTTPException(404, f"glossary not found at {_GLOSSARY_PATH}")
    text = _GLOSSARY_PATH.read_text(encoding="utf-8")
    parsed = _parse_glossary(text)
    parsed["path"] = str(_GLOSSARY_PATH.relative_to(_REPO_ROOT))
    return parsed
