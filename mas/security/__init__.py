"""v4.3 MAS — Security module.

Defense-in-depth utilities for protecting the Decision Engine from
brief-borne prompt injection and untrusted input. See policy.py for the
deterministic enforcement layer that complements this module.
"""
from .intake_sanitizer import (
    sanitize_brief,
    SanitizationResult,
    SanitizationFinding,
    Severity,
)

__all__ = [
    "sanitize_brief",
    "SanitizationResult",
    "SanitizationFinding",
    "Severity",
]
