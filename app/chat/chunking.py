"""Turns a document into the text that gets embedded.

One chunk per document today: the threshold below is far above the longest
body, so the paragraph split exists for the corpus growing, not for now.
"""
import hashlib

CHUNK_SPLIT_THRESHOLD_CHARS = 1_000   # measured 2026-09-25: longest body is 251 chars,
                                      # zero documents exceed 500 -- this never fires today


def embedded_text(title: str, body: str) -> str:
    """Title and body only. Provenance (`source` and the rest) is NOT
    embedded: `source` is a pinned exact literal, so "what does Walters say?"
    is a `WHERE source = ...` lookup, not a similarity search."""
    return f"{title}\n{body}"


def chunk_document(title: str, body: str, threshold: int = CHUNK_SPLIT_THRESHOLD_CHARS) -> list[str]:
    text = embedded_text(title, body)
    return [text] if len(text) <= threshold else _split_on_paragraphs(text, threshold)


def content_hash(text: str) -> str:
    """Hash of the embedded text -- title included. Hashing the body alone
    would miss a title-only edit and leave a stale vector silently."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_on_paragraphs(text: str, threshold: int) -> list[str]:
    """Greedily packs blank-line-separated paragraphs into chunks of at most
    `threshold` characters. A single paragraph longer than `threshold` is cut
    into `threshold`-sized pieces, so every chunk respects the limit."""
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
