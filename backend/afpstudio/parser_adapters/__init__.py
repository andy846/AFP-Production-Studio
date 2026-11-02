"""Parser adapters and the JSON contract they speak."""

from .contract import (  # noqa: F401
    CONTRACT_VERSION,
    AdapterError,
    AdapterResult,
    validate_result,
)

__all__ = [
    "CONTRACT_VERSION",
    "AdapterError",
    "AdapterResult",
    "validate_result",
]
