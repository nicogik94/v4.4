"""Tests for the shared evidence citation marker format."""
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.citation_format import (  # noqa: E402
    EVIDENCE_CITATION_MARKER_FORMAT,
    EVIDENCE_CITATION_MARKER_REGEX,
)


class TestCitationFormat(unittest.TestCase):
    def test_format_and_regex_named_groups_round_trip(self):
        marker = EVIDENCE_CITATION_MARKER_FORMAT.format(evidence_id="E12", locator="p.4")

        match = re.fullmatch(EVIDENCE_CITATION_MARKER_REGEX, marker)

        self.assertEqual(marker, "[Evidence: E12 | p.4]")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("evidence_id"), "E12")
        self.assertEqual(match.group("locator"), "p.4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
