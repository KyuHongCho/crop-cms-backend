"""The chat layer: embeddings, chunking, topic selection, and (later) generation.

Scores published content through the `published_item_chunks` view and names no raw
table; each topic's documents come from app/crud/retrieval.py.
tests/test_chat_layer_isolation.py is the tripwire for that convention; it is not
enforcement (see the README).
"""
