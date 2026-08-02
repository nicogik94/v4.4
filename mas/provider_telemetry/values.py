"""Truthful provider-value semantics.

Blocker 6 of the audit: "Floats, booleans and malformed values can be converted
into fabricated usage values or truthful data can be reported as absent." The
previous model had exactly two states for every provider field — a value, or
``None`` — and ``int(value)`` in between. That collapses six genuinely different
facts into two, and one of the six (``1.9`` silently becoming ``1``) is a
fabrication rather than a loss.

Every provider-sourced field in this package is therefore carried as a
:class:`ProviderValue`: a status drawn from a closed vocabulary, plus a value
that is populated *only* when the status is ``valid``.

======================  ====================================================
status                  meaning
======================  ====================================================
``absent``              the provider did not send this field at all
``null``                the provider sent the field with an explicit null
``valid``               the provider sent a value conforming to its contract
``invalid``             the provider sent something that violates its contract
``redacted``            a value was present but refused on safety grounds
``unsupported``         this provider/API has no such field to report
``unknown_value``       a well-formed value outside the closed vocabulary
======================  ====================================================

``absent`` and ``unsupported`` differ in an important way: OpenAI's Chat
Completions API has no cache-creation counter *at all*, which is ``unsupported``
and stays true forever; Anthropic omitting ``cache_creation_input_tokens`` on a
particular response is ``absent`` and may differ on the next call. Collapsing
them would make a paired experiment unable to tell a provider difference from a
response difference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALUE_ABSENT = "absent"
VALUE_NULL = "null"
VALUE_VALID = "valid"
VALUE_INVALID = "invalid"
VALUE_REDACTED = "redacted"
VALUE_UNSUPPORTED = "unsupported"
VALUE_UNKNOWN_VALUE = "unknown_value"

VALUE_STATUSES = (
    VALUE_ABSENT,
    VALUE_NULL,
    VALUE_VALID,
    VALUE_INVALID,
    VALUE_REDACTED,
    VALUE_UNSUPPORTED,
    VALUE_UNKNOWN_VALUE,
)

# A distinct "the attribute was not there at all" sentinel. ``None`` cannot
# serve: a provider may legitimately send ``stop_reason: null``, and the two
# must never masquerade as one another.
MISSING = object()

# Usage counters are stored in BIGINT columns but a genuine token count cannot
# approach even INT4. A larger value is a provider defect or an injection
# attempt, not a count, so it is refused rather than truncated.
MAX_USAGE_VALUE = 2_147_483_647


class ProviderValueError(ValueError):
    """A ProviderValue was constructed outside its closed contract."""


@dataclass(frozen=True)
class ProviderValue:
    """One provider-sourced field: a status, and a value only when valid."""

    status: str
    value: Any = None
    # For invalid/unknown/redacted values, a bounded, safe, non-reconstructible
    # note about *what kind* of thing was refused. Never the value itself unless
    # the grammar already proved it safe (unknown_value).
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALUE_STATUSES:
            raise ProviderValueError(f"unknown provider value status: {self.status!r}")
        if self.status != VALUE_VALID and self.value is not None:
            raise ProviderValueError(
                f"a {self.status} provider value must not carry a value"
            )
        if self.status == VALUE_VALID and self.value is None:
            raise ProviderValueError("a valid provider value must carry a value")

    @property
    def is_valid(self) -> bool:
        return self.status == VALUE_VALID

    @property
    def stored(self) -> Any:
        """What goes into the value column: the value, or NULL."""
        return self.value if self.status == VALUE_VALID else None

    def as_payload(self) -> dict[str, Any]:
        return {"status": self.status, "value": self.stored, "detail": self.detail}


ABSENT = ProviderValue(VALUE_ABSENT)
NULL = ProviderValue(VALUE_NULL)
UNSUPPORTED = ProviderValue(VALUE_UNSUPPORTED)


def valid(value: Any) -> ProviderValue:
    return ProviderValue(VALUE_VALID, value)


def invalid(detail: str = "") -> ProviderValue:
    return ProviderValue(VALUE_INVALID, None, detail=_detail(detail))


def redacted(detail: str = "") -> ProviderValue:
    return ProviderValue(VALUE_REDACTED, None, detail=_detail(detail))


def unknown_value(detail: str = "") -> ProviderValue:
    return ProviderValue(VALUE_UNKNOWN_VALUE, None, detail=_detail(detail))


_MAX_DETAIL = 64


def _detail(text: str) -> str:
    """Bound a diagnostic note to a short, printable, single-line token."""
    cleaned = "".join(ch for ch in str(text or "") if ch.isprintable() and ch != " ")
    return cleaned[:_MAX_DETAIL]


def exact_nonnegative_int(raw: Any) -> ProviderValue:
    """Classify a provider-supplied count.

    An exact nonnegative ``int`` and nothing else is valid. In particular:

    * ``True`` is not 1 — ``bool`` is a subclass of ``int`` in Python and would
      otherwise be silently accepted as a count;
    * ``1.9`` is not 1, and neither is ``1.0`` — a provider that sends a float
      where its own contract promises an integer has a defect worth recording,
      and rounding it would manufacture a number nobody reported;
    * ``"12"`` is not 12 — a numeric string in a JSON integer field means the
      response did not match the contract the reader is relying on;
    * a negative count and an absurdly large one are refused outright.
    """
    if raw is MISSING:
        return ABSENT
    if raw is None:
        return NULL
    if isinstance(raw, bool):
        return invalid("bool")
    if isinstance(raw, int):
        if raw < 0:
            return invalid("negative")
        if raw > MAX_USAGE_VALUE:
            return invalid("oversized")
        return valid(raw)
    if isinstance(raw, float):
        return invalid("float")
    if isinstance(raw, str):
        return invalid("string")
    return invalid(type(raw).__name__[:16])


def read_attribute(source: Any, name: str) -> Any:
    """Read ``name`` off a provider object or mapping, distinguishing absence.

    Returns :data:`MISSING` when the attribute genuinely is not there, which is
    what lets the callers above tell ``absent`` from ``null``.
    """
    if source is None or source is MISSING:
        return MISSING
    if isinstance(source, dict):
        return source.get(name, MISSING)
    return getattr(source, name, MISSING)
