"""The chat layer: embeddings, chunking, topic selection, and generation.

Reads the `published_item_chunks` view, never raw tables; tests/test_chat_layer_isolation.py is
a tripwire for that, not enforcement (see the README).
"""
