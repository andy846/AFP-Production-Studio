"""Printer profiles: the mapping from medium maps to real media."""

from .schema import ProfileError, PrinterProfile, validate_profile

__all__ = ["PrinterProfile", "ProfileError", "validate_profile"]
