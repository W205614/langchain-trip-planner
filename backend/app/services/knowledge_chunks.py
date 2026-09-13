"""Preserve section ownership and complete paragraph/table boundaries."""
import re
from types import SimpleNamespace
from .attraction_names import resolve_name


def sections(text):
    headings, lines = [], []
    def emit():
        body = "\n".join(lines).strip()
        return {"text": body, "heading_path": " / ".join(headings),
                "entity_name": headings[-1] if len(headings) >= 3 else ""}
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            if lines:
                yield emit()
            level, title = len(match[1]), match[2].strip()
            headings = headings[:level - 1] + [""] * max(0, level - 1 - len(headings)) + [title]
            lines = [line]
        else:
            lines.append(line)
    if lines:
        yield emit()


def complete_excerpt(text, limit):
    """Never return half a sentence or a partial table. Oversized blocks are omitted."""
    blocks = re.split(r"\n\s*\n", text.strip())
    selected = []
    for block in blocks:
        if len("\n\n".join([*selected, block])) > limit:
            break
        selected.append(block)
    return "\n\n".join(selected)


def entity_excerpt(document, name, city, limit, poi_id=None):
    meta = document.metadata
    if meta.get("poi_id") and poi_id:
        if meta["poi_id"] != poi_id:
            return ""
        return complete_excerpt(document.page_content, limit)
    owned = list(sections(document.page_content))
    if meta.get("entity_name"):
        owned = [{"entity_name": meta["entity_name"], "text": document.page_content}]
    candidates = [SimpleNamespace(id=str(i), name=s["entity_name"]) for i, s in enumerate(owned) if s["entity_name"]]
    match = resolve_name(name, candidates, city)
    return complete_excerpt(owned[int(match.id)]["text"], limit) if match else ""
