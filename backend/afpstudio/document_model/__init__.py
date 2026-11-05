"""The unified document model."""

__all__ = ["DocumentSession", "DocumentStore"]


def __getattr__(name):
    # Framing is used by the indexer; do not import the application model
    # (which itself imports the indexer) when loading this package.
    if name in __all__:
        from .model import DocumentSession, DocumentStore
        return {"DocumentSession": DocumentSession, "DocumentStore": DocumentStore}[name]
    raise AttributeError(name)
