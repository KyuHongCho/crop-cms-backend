"""Tripwire: app/chat/ must not name the ORM models or the raw tables (read the published_item_chunks view).

A convention, not enforcement: it greps source text, so a runtime-assembled name or an unlisted SQL
form (FROM ONLY items, a comma join) gets past it. Word boundaries: item_chunks is inside the view name.
"""
import pathlib
import re

import pytest

CHAT_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "chat"

FORBIDDEN = [re.compile(p, flags) for p, flags in (
    (r"\bItem\b", 0), (r"\bItemChunk\b", 0), (r"\bitem_chunks\b", 0),
    # the raw documents table carries the drafts the view filters out. Not a bare word-boundary
    # match on items, which dict.items() would trip: only SQL positions and Core's table("items", ...).
    (r"""\b(?:from|join|into|update)\s+(?:"?public"?\.)?["']?items\b""", re.IGNORECASE),
    (r"""\btable\(\s*["']items["']""", 0),
)]


def _violations(source: str) -> list[str]:
    return [pattern.pattern for pattern in FORBIDDEN if pattern.search(source)]


def test_the_chat_package_exists():
    # Guards the test below against passing vacuously on a moved directory.
    assert sorted(p.name for p in CHAT_DIR.glob("*.py")), f"no modules under {CHAT_DIR}"


@pytest.mark.parametrize("path", sorted(CHAT_DIR.rglob("*.py")), ids=lambda p: p.name)
def test_chat_module_does_not_name_the_raw_tables(path):
    found = _violations(path.read_text(encoding="utf-8"))
    assert not found, f"{path} names {found}; read the published_item_chunks view instead"


@pytest.mark.parametrize(
    "source, expected",
    [
        ("SELECT * FROM published_item_chunks", []),
        ("SELECT * FROM item_chunks", [r"\bitem_chunks\b"]),
        ("from app.model.model import Item", [r"\bItem\b"]),
        ("from app.model.model import ItemChunk", [r"\bItemChunk\b"]),
        ("ItemChunks, Items, items", []),
        ("for k, v in d.items():", []),
        ("SELECT * FROM items", [r"""\b(?:from|join|into|update)\s+(?:"?public"?\.)?["']?items\b"""]),
        ("JOIN items i ON i.id = c.item_id", [r"""\b(?:from|join|into|update)\s+(?:"?public"?\.)?["']?items\b"""]),
        ("SELECT content FROM public.items", [r"""\b(?:from|join|into|update)\s+(?:"?public"?\.)?["']?items\b"""]),
        ('SELECT * FROM "public"."items"', [r"""\b(?:from|join|into|update)\s+(?:"?public"?\.)?["']?items\b"""]),
        ("SELECT * FROM public.published_item_chunks", []),
        ('table("items", column("body"))', [r"""\btable\(\s*["']items["']"""]),
        ('table("published_item_chunks", column("embedding"))', []),
    ],
)
def test_the_patterns_match_names_not_substrings(source, expected):
    assert _violations(source) == expected
