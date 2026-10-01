"""The chat layer: embeddings, chunking, and (later) retrieval and generation.

Reads published content only, through the `published_item_chunks` view, never
the ORM models or the raw tables. tests/test_chat_layer_isolation.py is the
tripwire for that convention; it is not enforcement (see the README).
"""
