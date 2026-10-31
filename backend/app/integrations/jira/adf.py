"""Minimal Atlassian Document Format helpers."""

from typing import Any


def _text(t: str, bold: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": t}
    if bold:
        node["marks"] = [{"type": "strong"}]
    return node


def paragraph(text: str, bold: bool = False) -> dict[str, Any]:
    return {"type": "paragraph", "content": [_text(text or " ", bold)]}


def heading(text: str, level: int = 3) -> dict[str, Any]:
    return {"type": "heading", "attrs": {"level": level}, "content": [_text(text)]}


def bullet_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [paragraph(i)]} for i in items],
    }


def doc(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": list(blocks)}


def to_text(node: Any) -> str:
    """Flatten ADF to plain text (for comparisons and prior-version records)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        inner = to_text(node.get("content"))
        if node.get("type") in {"paragraph", "heading", "listItem"}:
            return inner + "\n"
        return inner
    return ""
