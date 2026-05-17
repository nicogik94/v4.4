"""Defensive text utilities for client delivery rendering."""

from __future__ import annotations

import json
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


def _is_iterable(value: Any) -> bool:
    return isinstance(value, Iterable)
