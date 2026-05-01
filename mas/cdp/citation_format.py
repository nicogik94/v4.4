"""Shared evidence citation marker format."""

EVIDENCE_CITATION_MARKER_FORMAT = "[Evidence: {evidence_id} | {locator}]"
EVIDENCE_CITATION_MARKER_LOCATOR_UNAVAILABLE = "locator unavailable"
EVIDENCE_CITATION_MARKER_REGEX = r"\[Evidence:\s+(?P<evidence_id>[^\s|]+)\s+\|\s+(?P<locator>[^\]]+)\]"
