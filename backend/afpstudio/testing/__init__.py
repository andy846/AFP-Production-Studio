"""Test support: synthetic AFP builders shared by the test suite."""

from .synthetic import (  # noqa: F401
    DocumentSpec,
    MediumMapSpec,
    conflicting_duplex_document,
    duplex_document,
    invalid_length_document,
    malformed_imm_document,
    no_form_def_document,
    simplex_document,
    transition_document,
    truncated_document,
    unknown_media_document,
)

__all__ = [
    "DocumentSpec",
    "MediumMapSpec",
    "conflicting_duplex_document",
    "duplex_document",
    "invalid_length_document",
    "malformed_imm_document",
    "no_form_def_document",
    "simplex_document",
    "transition_document",
    "truncated_document",
    "unknown_media_document",
]
