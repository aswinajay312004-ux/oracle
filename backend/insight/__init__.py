"""Data intelligence layer: metadata catalog, relationship graph, value index and the natural-language
question engine. Read-only by construction: it only ever compiles SELECT statements from validated
intents; it never executes model-written SQL."""
