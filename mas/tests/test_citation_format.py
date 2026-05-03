"""Tests for the shared evidence citation marker format."""
import re
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.citation_format import (  # noqa: E402
    EVIDENCE_CITATION_MARKER_FORMAT,
    EVIDENCE_CITATION_MARKER_REGEX,
    derive_knowledge_item_locator,
)


@dataclass
class _StubItem:
    """Minimal duck-typed stand-in for KnowledgeItem; attribute-only access."""

    structured_payload: dict[str, Any] = field(default_factory=dict)
    source_ref: str = ""
    locator: Any = None  # legacy explicit attribute; absent on the clean schema


def _item(**kwargs: Any) -> _StubItem:
    item = _StubItem()
    for key, value in kwargs.items():
        setattr(item, key, value)
    return item


class TestCitationFormat(unittest.TestCase):
    def test_format_and_regex_named_groups_round_trip(self):
        marker = EVIDENCE_CITATION_MARKER_FORMAT.format(evidence_id="E12", locator="p.4")

        match = re.fullmatch(EVIDENCE_CITATION_MARKER_REGEX, marker)

        self.assertEqual(marker, "[Evidence: E12 | p.4]")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("evidence_id"), "E12")
        self.assertEqual(match.group("locator"), "p.4")


class TestDeriveKnowledgeItemLocator(unittest.TestCase):
    def test_explicit_attribute_wins(self):
        item = _item(locator="legacy=1", structured_payload={"locator": "ignored", "chunk_index": 9})

        self.assertEqual(derive_knowledge_item_locator(item), "legacy=1")

    def test_structured_payload_explicit_locator(self):
        item = _item(structured_payload={"locator": "page=4"})

        self.assertEqual(derive_knowledge_item_locator(item), "page=4")

    def test_chunk_index_renders_chunk_n(self):
        item = _item(structured_payload={"chunk_index": 7})

        self.assertEqual(derive_knowledge_item_locator(item), "chunk=7")

    def test_row_range_without_sheet(self):
        item = _item(structured_payload={"row_start": 2, "row_end": 17})

        self.assertEqual(derive_knowledge_item_locator(item), "row=2-17")

    def test_row_range_with_sheet(self):
        item = _item(structured_payload={"row_start": 2, "row_end": 17, "sheet_name": "Q3"})

        self.assertEqual(derive_knowledge_item_locator(item), "sheet=Q3;row=2-17")

    def test_page_only(self):
        item = _item(structured_payload={"page": 5})

        self.assertEqual(derive_knowledge_item_locator(item), "page=5")

    def test_source_ref_fragment_chunk(self):
        item = _item(source_ref="upload:f1:doc.pdf#chunk=3")

        self.assertEqual(derive_knowledge_item_locator(item), "chunk=3")

    def test_source_ref_fragment_rows(self):
        item = _item(source_ref="upload:f1:sheet.csv#rows=10-20")

        self.assertEqual(derive_knowledge_item_locator(item), "rows=10-20")

    def test_unknown_returns_empty_string(self):
        item = _item(structured_payload={"category": "uploaded_document", "filename": "x.pdf"})

        self.assertEqual(derive_knowledge_item_locator(item), "")

    def test_no_payload_no_source_ref_returns_empty_string(self):
        self.assertEqual(derive_knowledge_item_locator(_item()), "")

    def test_payload_explicit_locator_beats_anchor_fields(self):
        item = _item(structured_payload={"locator": "page=4", "chunk_index": 9})

        self.assertEqual(derive_knowledge_item_locator(item), "page=4")

    def test_source_ref_fragment_ignored_when_payload_anchor_present(self):
        item = _item(structured_payload={"chunk_index": 7}, source_ref="upload:f1:doc.pdf#chunk=99")

        self.assertEqual(derive_knowledge_item_locator(item), "chunk=7")

    def test_non_locator_fragment_returns_empty(self):
        item = _item(source_ref="upload:f1:doc.pdf#section-intro")

        self.assertEqual(derive_knowledge_item_locator(item), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
