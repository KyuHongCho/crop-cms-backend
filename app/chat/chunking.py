"""Turns a document into the text that gets embedded.

One chunk per document today; the paragraph split exists for the corpus growing.
"""
import hashlib

# measured 2026-10-03: longest embedded text is 874 chars over 62 documents, so this never fires
CHUNK_SPLIT_THRESHOLD_CHARS = 1_000


def embedded_text(title: str, body: str) -> str:
    """Title and body only. Provenance is NOT embedded: `source` is a pinned literal, so
    "what does Walters say?" is a `WHERE source = ...` lookup, not a similarity search."""
    return f"{title}\n{body}"


def chunk_document(title: str, body: str, threshold: int = CHUNK_SPLIT_THRESHOLD_CHARS) -> list[str]:
    text = embedded_text(title, body)
    return [text] if len(text) <= threshold else _split_on_paragraphs(text, threshold)


def content_hash(text: str) -> str:
    """Hash of the embedded text, title included: a body-only hash would miss a title-only edit."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_on_paragraphs(text: str, threshold: int) -> list[str]:
    """Greedily packs blank-line-separated paragraphs into chunks of at most `threshold`
    characters; an over-long paragraph is cut into `threshold`-sized pieces."""
    chunks: list[str] = []
    current = ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        pieces = [paragraph[i:i + threshold] for i in range(0, len(paragraph), threshold)]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= threshold:
                current = candidate
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks
