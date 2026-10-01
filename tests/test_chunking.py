"""app/chat/chunking.py: what text is embedded, how it is hashed and split.

Pure functions -- nothing here touches the database beyond conftest.py's
autouse TRUNCATE.
"""
from app.chat.chunking import (
    CHUNK_SPLIT_THRESHOLD_CHARS,
    chunk_document,
    content_hash,
    embedded_text,
)
from scripts import seed


def test_short_document_is_one_chunk_of_title_and_body():
    assert chunk_document("A title", "A body.") == ["A title\nA body."]


def test_title_only_edit_changes_the_hash():
    """Why the hash covers title + body: the body is unchanged by a title-only
    edit, so a body-only hash could not see it and would leave a stale vector
    silently."""
    body = "Basil prefers evenly moist soil."
    before, after = embedded_text("Old title", body), embedded_text("New title", body)
    assert content_hash(before) != content_hash(after)


def test_content_hash_is_sha256_hex():
    digest = content_hash("x")
    assert len(digest) == 64 and int(digest, 16) >= 0


def test_long_document_splits_on_paragraphs_within_the_threshold():
    paragraphs = [f"paragraph {n} " + "x" * 400 for n in range(5)]
    chunks = chunk_document("Title", "\n\n".join(paragraphs))
    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_SPLIT_THRESHOLD_CHARS for chunk in chunks)
    joined = "\n\n".join(chunks)
    assert all(paragraph in joined for paragraph in paragraphs)


def test_a_single_paragraph_over_the_threshold_is_still_cut_to_size():
    chunks = chunk_document("T", "y" * 2_500, threshold=1_000)
    assert len(chunks) == 3
    assert all(len(chunk) <= 1_000 for chunk in chunks)
    assert "".join(chunks) == "T\n" + "y" * 2_500


def test_threshold_never_fires_on_the_seed_corpus():
    """The comment beside CHUNK_SPLIT_THRESHOLD_CHARS says the split never
    fires today; this keeps that true of the seed corpus, or makes it fail
    loudly when the corpus outgrows it."""
    for _, docs in seed._TOPIC_GROUPS:
        for doc in docs:
            assert len(chunk_document(doc["title"], doc["body"])) == 1, doc["title"]
