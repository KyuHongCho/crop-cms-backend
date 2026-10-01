"""Tripwire: app/chat/ must not name the ORM models or the raw tables.

The chat layer reads published content through the `published_item_chunks`
view. This test is a convention with a tripwire, not enforcement: it greps
source text, so a name assembled at run time would get past it, and the
database itself still lets the application role read every table.

Word boundaries are required, not a substring search: `"item_chunks" in src`
also matches the legitimate view name `published_item_chunks` and would fail
on correct code. `\\bitem_chunks\\b` does not match inside it, because `_` is a
word character.
"""
import pathlib
import re

import pytest

CHAT_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "chat"

FORBIDDEN = [re.compile(p) for p in (r"\bItem\b", r"\bItemChunk\b", r"\bitem_chunks\b")]


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
    ],
)
def test_the_patterns_match_names_not_substrings(source, expected):
    assert _violations(source) == expected
