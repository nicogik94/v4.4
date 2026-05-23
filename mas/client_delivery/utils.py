"""Defensive text utilities for client delivery rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID


def safe_text(value: Any, default: str = "") -> str:
    """Return a stable text representation for sparse or mixed-type values."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple, set)):
        return safe_join(list(value))
    try:
        return str(value)
    except Exception:
        return default


def safe_join(values: Any, sep: str = ", ") -> str:
    """Join mixed values without assuming a list or string-only contents."""
    if values is None:
        return ""
    if isinstance(values, (str, bytes, dict)) or not _is_iterable(values):
        return safe_text(values)
    parts: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            nested = safe_join(value, sep=sep)
            if nested:
                parts.append(nested)
        else:
            text = safe_text(value)
            if text:
                parts.append(text)
    return sep.join(parts)


def display_text(value: Any, default: str = "") -> str:
    """Return deterministic client-visible text without JSON/Python object syntax."""
    if value is None:
        return default
    if isinstance(value, str):
        return _redact_client_visible(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value, key=lambda item: safe_text(item)):
            text = display_text(value.get(key))
            if text:
                parts.append(text)
        return _redact_client_visible(", ".join(parts) or default)
    if isinstance(value, (list, tuple)):
        return display_join(value)
    if isinstance(value, set):
        return display_join(sorted(value, key=lambda item: display_text(item)))
    return _redact_client_visible(safe_text(value, default=default))


def display_join(values: Any, sep: str = ", ") -> str:
    """Join mixed values for client-visible artifacts using display-safe text."""
    if values is None:
        return ""
    if isinstance(values, (str, bytes, dict)) or not _is_iterable(values):
        return display_text(values)
    parts: list[str] = []
    for value in values:
        text = display_text(value)
        if text:
            parts.append(text)
    return sep.join(parts)


def spreadsheet_text(value: Any) -> str:
    """Return a spreadsheet-safe string for client-visible text cells."""
    text = display_text(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _redact_client_visible(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s,;|)>\]]+",
        "credential=[redacted]",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bsource_ref\s*[:=]\s*[^\s|,)>\]]+", "Evidence source unavailable", text, flags=re.I)
    text = re.sub(r"\bstorage_ref\s*[:=]\s*[^\s|,)>\]]+", "Evidence source unavailable", text, flags=re.I)
    text = re.sub(r"\bupload:[^\s|,)>\]]+", "Uploaded project document", text, flags=re.I)
    text = re.sub(r"\bknowledge[_-][A-Za-z0-9_.:-]+\b", "project evidence", text, flags=re.I)
    text = re.sub(r"\b(?:ev|evidence|src)-[A-Za-z0-9_.:-]+\b", "project evidence", text, flags=re.I)
    text = re.sub(r"\b[A-Za-z]:[\\/][^\s,;|)>\]]+", "redacted local path", text)
    text = re.sub(r"\\\\[^\s,;|)>\]]+", "redacted local path", text)
    return text


def _is_iterable(value: Any) -> bool:
    return isinstance(value, Iterable)
