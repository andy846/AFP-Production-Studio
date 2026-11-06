"""AFP Production Studio — local AFP viewer, inspector and media preflight.

The package is deliberately import-safe: nothing here touches the
filesystem, the network or Flask at import time, so the individual
subsystems can be unit tested without a running server.
"""

__version__ = "0.3.0"

# Contract version spoken by the Alpheus adapter. Bump the minor part for
# additive changes (new optional fields) and the major part whenever an
# existing field changes meaning.
ADAPTER_CONTRACT_VERSION = "2.0.0"

__all__ = ["__version__", "ADAPTER_CONTRACT_VERSION"]
