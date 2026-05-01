"""Focused T1a Gate 2 citation-marker validation tests."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_t1a_gate2 import CANONICAL_MARKER_RE, _malformed_candidates  # noqa: E402


class TestT1aGate2Validation(unittest.TestCase):
    def test_literal_pipe_marker_matches(self):
        marker = "[Evidence: ev-market-note | chunk=2]"

        self.assertIsNotNone(CANONICAL_MARKER_RE.fullmatch(marker))

    def test_escaped_pipe_marker_does_not_match(self):
        marker = "[Evidence: ev-market-note \\| chunk=2]"

        self.assertIsNone(CANONICAL_MARKER_RE.fullmatch(marker))

    def test_escaped_pipe_marker_is_reported_as_malformed(self):
        marker = "[Evidence: ev-market-note \\| chunk=2]"

        self.assertEqual(_malformed_candidates(f"Claim text {marker}."), [marker])

    def test_placeholder_markers_are_reported_as_malformed(self):
        markers = [
            "[Evidence: ...]",
            "[Evidence: <evidence_id> | <locator>]",
            "[Evidence: evidence_id | locator]",
            "[Evidence: ev-market-note | ...]",
            "[Evidence: ... | ...]",
        ]

        for marker in markers:
            with self.subTest(marker=marker):
                self.assertEqual(_malformed_candidates(f"Claim text {marker}."), [marker])


if __name__ == "__main__":
    unittest.main(verbosity=2)
