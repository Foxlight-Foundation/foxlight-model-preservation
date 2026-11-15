"""Every link to a heading in another document names a heading that exists."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    *ROOT.glob("*.md"),
    *(ROOT / "docs").rglob("*.md"),
    *(ROOT / "capability").glob("*.md"),
]
LINK = re.compile(r"\]\(([^)\s]+\.md)#([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~)")


def slug(heading: str) -> str:
    """GitHub's (and Docusaurus's) heading anchor."""
    markup = r"[`*_]|<[^>]+>|\[([^\]]*)\]\([^)]*\)"
    text = re.sub(markup, r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: dict[str, int] = {}
    result: set[str] = set()
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        match = None if fenced else re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match:
            base = slug(match.group(1))
            count = found.get(base, 0)
            found[base] = count + 1
            result.add(base if count == 0 else f"{base}-{count}")
    return result


def test_links_to_headings_resolve() -> None:
    broken = []
    for document in DOCUMENTS:
        for target, fragment in LINK.findall(document.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            path = (document.parent / target).resolve()
            if path.is_file() and fragment not in anchors(path):
                broken.append(f"{document.relative_to(ROOT)} -> {target}#{fragment}")
    assert not broken, "links to missing headings:\n" + "\n".join(broken)
