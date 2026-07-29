"""Cross-project determinism regression tests for controlled knowledge retrieval.

D001 demonstrated that two projects holding the *same* Knowledge bytes could
receive different prompt-facing subsets. Eligible items were ordered by
``(observed_at, item_id)``; every chunk of one upload shares a single
``observed_at``, so the tie-break was always ``item_id`` — which derives from
``source_id``/``file_id``, and therefore from the project UUID plus wall-clock
and random material. Once ``max_items`` truncated the list, two projects kept
different chunks of the same document.

These tests pin the fixed contract: recency stays primary, and ties break on the
item's own content identity (``checksum_sha256``), never on a project-local
opaque identifier.
"""
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from config import UPLOAD_LAYER  # noqa: E402
from knowledge import retrieval  # noqa: E402
from knowledge.files import ingest_uploaded_file  # noqa: E402
from knowledge.projection import ProjectedKnowledgeItem  # noqa: E402
from knowledge.registry import ensure_knowledge_layer, upsert_source_entry  # noqa: E402
from knowledge.retrieval import (  # noqa: E402
    PROMPT_FACING_RETRIEVAL_PHASES,
    PhaseKnowledgeRetrievalView,
    RetrievalEligibleItem,
    build_phase_retrieval_impact,
    build_project_retrieval_summary,
    build_prompt_facing_retrieval_impact,
    evaluate_phase_retrieval,
    get_retrieval_policy,
)
from knowledge.sync import sync_offline_source  # noqa: E402
from state import KnowledgeItemStatus, SourceRegistryEntry  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Prompt-facing item titles embed the uploaded filename verbatim (see
# TestFilenameBoundary). The future D001 recreation must upload this exact
# filename, in this exact Unicode normalization form.
DOCUMENT_FILENAME = "CACOFÓNICO.docx"

PROJECT_A = "3f4b9c0e-6d21-4a7f-9d0b-8e5c2a1f7b40"
PROJECT_B = "b71d6a52-0c38-4e19-8f26-5a9d34c7e081"
UPLOAD_MATERIAL_A = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
UPLOAD_MATERIAL_B = "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"

UPLOADED_AT_A = datetime(2026, 7, 20, 9, 15, 0)
UPLOADED_AT_B = datetime(2026, 7, 20, 17, 42, 30)
EVALUATED_AT = datetime(2026, 7, 20, 21, 0, 0)

# The certified CACOFÓNICO upload parsed into 9 knowledge items against
# audit/strategy max_items=6. The fixture below reproduces that shape.
EXPECTED_ITEM_COUNT = 9
EXPECTED_MAX_ITEMS = 6


# ═══ Fixture: one document, many chunks, no project-local material ═══


def _segment(index: int) -> str:
    body = f"Hallazgo {index}: la señal de demanda del bloque {index} cambia de forma medible. "
    return (f"[Bloque {index:02d}] " + body * 30)[: UPLOAD_LAYER.document_chunk_chars]


_DOCUMENT_BYTES: bytes | None = None


def _document_bytes() -> bytes:
    """Deterministic multi-chunk DOCX; parsing yields EXPECTED_ITEM_COUNT chunks."""
    global _DOCUMENT_BYTES
    if _DOCUMENT_BYTES is None:
        from docx import Document

        buffer = io.BytesIO()
        document = Document()
        document.add_paragraph("".join(_segment(index) for index in range(1, 11)))
        document.save(buffer)
        _DOCUMENT_BYTES = buffer.getvalue()
    return _DOCUMENT_BYTES


def _frozen_datetime(moment: datetime):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    return _FrozenDatetime


class _FrozenUuidModule:
    """Fixed (but per-project distinct) random material for upload file ids.

    This does not normalize identities: each project still derives different
    file/source/item ids. It only removes run-to-run randomness so the
    pre-fix contrast below is a deterministic assertion.
    """

    def __init__(self, hex_value: str):
        self._hex = hex_value

    def uuid4(self):
        return type("_FrozenUuid", (), {"hex": self._hex})()


def _uploaded_state(
    project_id: str,
    *,
    uploaded_at: datetime,
    random_material: str,
    filename: str = DOCUMENT_FILENAME,
    content: bytes | None = None,
):
    """Ingest the document through the normal upload/parsing path."""
    state = make_state(project_id)
    with tempfile.TemporaryDirectory() as tempdir, \
            patch.object(UPLOAD_LAYER, "storage_dir", tempdir), \
            patch("knowledge.files.datetime", _frozen_datetime(uploaded_at)), \
            patch("knowledge.files.uuid", _FrozenUuidModule(random_material)):
        ingest_uploaded_file(
            state,
            filename=filename,
            media_type=DOCX_MEDIA_TYPE,
            content=_document_bytes() if content is None else content,
            actor="operator",
        )
    return state


# ═══ Semantic (project-independent) view of a phase selection ═══


def _semantic_selection(state, phase: str, *, now: datetime = EVALUATED_AT) -> dict:
    view = evaluate_phase_retrieval(state, phase, now=now)
    items_by_id = {item.item_id: item for item in state.knowledge_layer.items}
    projection_rows = []
    chunk_indices = []
    checksums = []
    for record in view.eligible_items:
        item = items_by_id[record.item_id]
        chunk_indices.append(item.structured_payload.get("chunk_index"))
        checksums.append(item.checksum_sha256)
        projection_rows.append(
            {
                "source_name": record.projection.source_name,
                "title": record.projection.title,
                "summary": record.projection.summary,
                "facts": [[fact.key, fact.value] for fact in record.projection.facts],
                "freshness_status": record.projection.freshness_status,
                "trust_tier": record.projection.trust_tier,
                "sensitivity": record.projection.sensitivity,
                "untrusted_source": record.projection.untrusted_source,
            }
        )
    return {
        "view": view,
        "chunk_indices": chunk_indices,
        "checksums": checksums,
        "projection_rows": projection_rows,
        "projection_digest": _digest(projection_rows),
        "item_ids": [record.item_id for record in view.eligible_items],
        "source_ids": [record.source_id for record in view.eligible_items],
        "observed_at": [record.projection.observed_at for record in view.eligible_items],
        "max_items": view.policy.prompt_exposure.max_items,
    }


def _digest(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _legacy_chunk_sequence(state, *, max_items: int = EXPECTED_MAX_ITEMS) -> list:
    """Reproduce the pre-fix ``(observed_at, item_id)`` ordering for contrast.

    Only valid for fixtures where every item is eligible, which is the case for
    the single-upload states used here.
    """
    ordered = sorted(
        state.knowledge_layer.items,
        key=lambda item: (item.observed_at, item.item_id),
        reverse=True,
    )
    return [item.structured_payload.get("chunk_index") for item in ordered[:max_items]]


class TestCrossProjectRetrievalEquivalence(unittest.TestCase):
    """MANDATORY: same bytes + same filename => same prompt-facing subset."""

    @classmethod
    def setUpClass(cls):
        cls.state_a = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        cls.state_b = _uploaded_state(
            PROJECT_B, uploaded_at=UPLOADED_AT_B, random_material=UPLOAD_MATERIAL_B
        )

    def test_fixture_reproduces_the_d001_failure_mode(self):
        """The two projects really are independent, and the old key really did differ."""
        self.assertNotEqual(self.state_a.project_id, self.state_b.project_id)

        file_ids = {
            state.knowledge_layer.uploaded_files[0].file_id
            for state in (self.state_a, self.state_b)
        }
        source_ids = {
            state.knowledge_layer.sources[0].source_id
            for state in (self.state_a, self.state_b)
        }
        self.assertEqual(len(file_ids), 2)
        self.assertEqual(len(source_ids), 2)
        self.assertEqual(
            set(item.item_id for item in self.state_a.knowledge_layer.items)
            & set(item.item_id for item in self.state_b.knowledge_layer.items),
            set(),
        )
        self.assertNotEqual(
            self.state_a.knowledge_layer.items[0].observed_at,
            self.state_b.knowledge_layer.items[0].observed_at,
        )

        # Same original bytes and same filename on both sides.
        self.assertEqual(
            self.state_a.knowledge_layer.uploaded_files[0].checksum_sha256,
            self.state_b.knowledge_layer.uploaded_files[0].checksum_sha256,
        )
        self.assertEqual(
            {state.knowledge_layer.uploaded_files[0].filename for state in (self.state_a, self.state_b)},
            {DOCUMENT_FILENAME},
        )

        # More eligible chunks than max_items, so truncation is exercised.
        for state in (self.state_a, self.state_b):
            self.assertEqual(len(state.knowledge_layer.items), EXPECTED_ITEM_COUNT)
        self.assertGreater(EXPECTED_ITEM_COUNT, EXPECTED_MAX_ITEMS)

        # Pre-fix ordering key: different subsets from identical knowledge.
        self.assertNotEqual(
            _legacy_chunk_sequence(self.state_a),
            _legacy_chunk_sequence(self.state_b),
        )

    def test_audit_and_strategy_select_the_same_semantic_subset(self):
        for phase in ("audit", "strategy"):
            with self.subTest(phase=phase):
                selection_a = _semantic_selection(self.state_a, phase)
                selection_b = _semantic_selection(self.state_b, phase)

                self.assertEqual(selection_a["chunk_indices"], selection_b["chunk_indices"])
                self.assertEqual(selection_a["checksums"], selection_b["checksums"])
                self.assertEqual(selection_a["projection_rows"], selection_b["projection_rows"])
                self.assertEqual(selection_a["projection_digest"], selection_b["projection_digest"])

                # max_items unchanged, and truncation actually applied.
                self.assertEqual(selection_a["max_items"], EXPECTED_MAX_ITEMS)
                self.assertEqual(selection_b["max_items"], EXPECTED_MAX_ITEMS)
                self.assertEqual(len(selection_a["chunk_indices"]), EXPECTED_MAX_ITEMS)
                self.assertEqual(len(selection_a["view"].blocked_items), 0)
                self.assertEqual(len(selection_b["view"].blocked_items), 0)

                # Equivalence holds despite genuinely different opaque identities.
                self.assertNotEqual(selection_a["item_ids"], selection_b["item_ids"])
                self.assertNotEqual(selection_a["source_ids"], selection_b["source_ids"])
                self.assertNotEqual(selection_a["observed_at"], selection_b["observed_at"])

                # Selected chunks are a strict, distinct subset of the document.
                self.assertEqual(len(set(selection_a["chunk_indices"])), EXPECTED_MAX_ITEMS)
                self.assertEqual(len(set(selection_a["checksums"])), EXPECTED_MAX_ITEMS)

    def test_audit_and_strategy_agree_with_each_other(self):
        """Both prompt-facing phases share one policy, so both keep one subset."""
        audit = _semantic_selection(self.state_a, "audit")
        strategy = _semantic_selection(self.state_a, "strategy")
        self.assertEqual(audit["chunk_indices"], strategy["chunk_indices"])
        self.assertEqual(audit["checksums"], strategy["checksums"])
        self.assertEqual(audit["projection_digest"], strategy["projection_digest"])


class TestRetrievalRepeatability(unittest.TestCase):
    """A. Repeated evaluation of the same state returns identical ordering."""

    def test_repeated_evaluation_is_identical(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        for phase in ("audit", "strategy"):
            with self.subTest(phase=phase):
                runs = [_semantic_selection(state, phase) for _ in range(3)]
                for run in runs[1:]:
                    self.assertEqual(run["item_ids"], runs[0]["item_ids"])
                    self.assertEqual(run["chunk_indices"], runs[0]["chunk_indices"])
                    self.assertEqual(run["checksums"], runs[0]["checksums"])
                    self.assertEqual(run["projection_digest"], runs[0]["projection_digest"])


class TestOpaqueIdIndependence(unittest.TestCase):
    """B. Changing only opaque identity cannot change the semantic selection."""

    @staticmethod
    def _rewrite_opaque_identity(state, *, project_id: str, source_id: str, item_ids: list[str]):
        mutated = state.model_copy(deep=True)
        mutated.project_id = project_id
        mutated.knowledge_layer.sources[0].source_id = source_id
        for manifest in mutated.knowledge_layer.uploaded_files:
            manifest.source_id = source_id
        for item, new_item_id in zip(mutated.knowledge_layer.items, item_ids):
            item.item_id = new_item_id
            item.source_id = source_id
        mutated.knowledge_layer.items.reverse()
        return mutated

    def test_opaque_identity_rewrite_preserves_semantic_selection(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        count = len(state.knowledge_layer.items)
        ascending = self._rewrite_opaque_identity(
            state,
            project_id="0d4b1d6c-9a3e-4e2b-9c77-1f0a2b3c4d5e",
            source_id="knowledge_source_ascending",
            item_ids=[f"knowledge_asc_{index:04d}" for index in range(count)],
        )
        descending = self._rewrite_opaque_identity(
            state,
            project_id="7c2e8f11-52ab-4c6d-8e90-3a4b5c6d7e8f",
            source_id="knowledge_source_descending",
            item_ids=[f"knowledge_desc_{count - 1 - index:04d}" for index in range(count)],
        )

        # Non-vacuous by construction: the pre-fix key ranked these two states
        # in opposite directions, so it would have kept different chunks.
        self.assertNotEqual(
            _legacy_chunk_sequence(ascending),
            _legacy_chunk_sequence(descending),
        )

        for phase in ("audit", "strategy"):
            with self.subTest(phase=phase):
                baseline = _semantic_selection(state, phase)
                for mutated in (ascending, descending):
                    selection = _semantic_selection(mutated, phase)
                    self.assertEqual(selection["chunk_indices"], baseline["chunk_indices"])
                    self.assertEqual(selection["checksums"], baseline["checksums"])
                    self.assertEqual(selection["projection_rows"], baseline["projection_rows"])
                    self.assertEqual(selection["projection_digest"], baseline["projection_digest"])

    def test_ordering_key_carries_no_project_local_identifier(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)
        items_by_id = {item.item_id: item for item in state.knowledge_layer.items}
        file_id = state.knowledge_layer.uploaded_files[0].file_id

        for record in view.eligible_items:
            item = items_by_id[record.item_id]
            key = retrieval._retrieval_ordering_key(item, record.projection)
            self.assertEqual(key, (record.projection.observed_at, item.checksum_sha256))
            for opaque in (item.item_id, item.source_id, file_id, state.project_id):
                self.assertNotIn(opaque, key[1])

class TestChecksumlessFallbackIdentity(unittest.TestCase):
    """``checksum_sha256`` defaults to empty, so the fallback is real state.

    The fallback digest must be derived from semantic content alone. It must not
    hash ``observed_at``: recency is already the primary ordering component, and
    hashing it would let semantically identical items rank differently across
    equivalent states.
    """

    OBSERVED_BASE_A = EVALUATED_AT - timedelta(hours=20)
    OBSERVED_BASE_B = EVALUATED_AT - timedelta(hours=8)

    @staticmethod
    def _checksumless_state(
        project_id: str,
        *,
        uploaded_at: datetime,
        random_material: str,
        observed_base: datetime,
        item_id_prefix: str,
        invert_item_ids: bool,
    ):
        """Same semantic content, same relative recency structure, own identities.

        Recency tiers are a function of the chunk's own content (its index), so
        both projects carry the same relative recency structure over a
        project-specific base timestamp.
        """
        state = _uploaded_state(project_id, uploaded_at=uploaded_at, random_material=random_material)
        count = len(state.knowledge_layer.items)
        for item in state.knowledge_layer.items:
            chunk_index = int(item.structured_payload["chunk_index"])
            item.checksum_sha256 = ""
            item.observed_at = (observed_base + timedelta(hours=chunk_index % 3)).isoformat()
            item.captured_at = item.observed_at
            rank = count - chunk_index if invert_item_ids else chunk_index
            item.item_id = f"{item_id_prefix}_{rank:04d}"
        return state

    @staticmethod
    def _recency_tiers(chunk_indices: list) -> list:
        return [int(index) % 3 for index in chunk_indices]

    # ── A. single-item semantic identity ───────────────────────────────────

    def test_identity_ignores_opaque_ids_and_timestamps(self):
        state_a = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        state_b = _uploaded_state(
            PROJECT_B, uploaded_at=UPLOADED_AT_B, random_material=UPLOAD_MATERIAL_B
        )
        self.assertNotEqual(state_a.project_id, state_b.project_id)

        item = state_a.knowledge_layer.items[0].model_copy(deep=True)
        item.checksum_sha256 = ""
        twin = state_b.knowledge_layer.items[0].model_copy(deep=True)
        twin.checksum_sha256 = ""
        # Same semantic content, everything instance-specific different.
        twin.title = item.title
        twin.summary = item.summary
        twin.structured_payload = dict(item.structured_payload)
        twin.item_id = "knowledge_some_other_opaque_id"
        twin.source_id = "knowledge_source_other"
        twin.source_ref = "upload:file_other:other#chunk=1"
        twin.observed_at = (EVALUATED_AT - timedelta(hours=37)).isoformat()
        twin.captured_at = twin.observed_at

        self.assertNotEqual(item.item_id, twin.item_id)
        self.assertNotEqual(item.source_id, twin.source_id)
        self.assertNotEqual(item.observed_at, twin.observed_at)
        self.assertEqual(retrieval._semantic_identity(item), retrieval._semantic_identity(twin))

        identity = retrieval._semantic_identity(item)
        for opaque in (item.item_id, item.source_id, item.observed_at, state_a.project_id):
            self.assertNotIn(opaque, identity)

    def test_observed_at_alone_never_changes_identity(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        item = state.knowledge_layer.items[0].model_copy(deep=True)
        item.checksum_sha256 = ""
        baseline = retrieval._semantic_identity(item)

        for hours in (1, 24, 10_000):
            shifted = item.model_copy(deep=True)
            shifted.observed_at = (EVALUATED_AT - timedelta(hours=hours)).isoformat()
            shifted.captured_at = shifted.observed_at
            shifted.effective_at = shifted.observed_at
            with self.subTest(hours=hours):
                self.assertNotEqual(shifted.observed_at, item.observed_at)
                self.assertEqual(retrieval._semantic_identity(shifted), baseline)

    def test_semantic_content_changes_do_change_identity(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        item = state.knowledge_layer.items[0].model_copy(deep=True)
        item.checksum_sha256 = ""
        baseline = retrieval._semantic_identity(item)

        mutations = {
            "title": lambda copy: setattr(copy, "title", copy.title + " (edited)"),
            "summary": lambda copy: setattr(copy, "summary", copy.summary + " (edited)"),
            "structured_payload": lambda copy: copy.structured_payload.update({"category": "changed"}),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                mutated = item.model_copy(deep=True)
                mutate(mutated)
                self.assertNotEqual(retrieval._semantic_identity(mutated), baseline)

    # ── B. truncated multi-item fallback equivalence ───────────────────────

    def test_checksumless_projects_select_the_same_semantic_subset(self):
        state_a = self._checksumless_state(
            PROJECT_A,
            uploaded_at=UPLOADED_AT_A,
            random_material=UPLOAD_MATERIAL_A,
            observed_base=self.OBSERVED_BASE_A,
            item_id_prefix="knowledge_fallback_a",
            invert_item_ids=False,
        )
        state_b = self._checksumless_state(
            PROJECT_B,
            uploaded_at=UPLOADED_AT_B,
            random_material=UPLOAD_MATERIAL_B,
            observed_base=self.OBSERVED_BASE_B,
            item_id_prefix="knowledge_fallback_b",
            invert_item_ids=True,
        )

        # Non-vacuous: fallback path really is exercised, truncation really bites,
        # and every instance-specific identity genuinely differs.
        for state in (state_a, state_b):
            self.assertEqual(len(state.knowledge_layer.items), EXPECTED_ITEM_COUNT)
            self.assertTrue(all(not item.checksum_sha256 for item in state.knowledge_layer.items))
        self.assertGreater(EXPECTED_ITEM_COUNT, EXPECTED_MAX_ITEMS)
        self.assertNotEqual(state_a.project_id, state_b.project_id)
        self.assertEqual(
            {item.item_id for item in state_a.knowledge_layer.items}
            & {item.item_id for item in state_b.knowledge_layer.items},
            set(),
        )
        self.assertNotEqual(
            state_a.knowledge_layer.sources[0].source_id,
            state_b.knowledge_layer.sources[0].source_id,
        )
        self.assertEqual(
            {item.observed_at for item in state_a.knowledge_layer.items}
            & {item.observed_at for item in state_b.knowledge_layer.items},
            set(),
        )
        # The pre-fix key ordered these two states in opposite directions.
        self.assertNotEqual(_legacy_chunk_sequence(state_a), _legacy_chunk_sequence(state_b))

        for phase in ("audit", "strategy"):
            with self.subTest(phase=phase):
                selection_a = _semantic_selection(state_a, phase)
                selection_b = _semantic_selection(state_b, phase)

                self.assertEqual(selection_a["chunk_indices"], selection_b["chunk_indices"])
                self.assertEqual(selection_a["projection_rows"], selection_b["projection_rows"])
                self.assertEqual(selection_a["projection_digest"], selection_b["projection_digest"])
                self.assertEqual(selection_a["max_items"], EXPECTED_MAX_ITEMS)
                self.assertEqual(len(selection_a["chunk_indices"]), EXPECTED_MAX_ITEMS)
                self.assertEqual(len(selection_a["view"].blocked_items), 0)
                self.assertEqual(len(selection_b["view"].blocked_items), 0)
                self.assertNotEqual(selection_a["item_ids"], selection_b["item_ids"])
                self.assertNotEqual(selection_a["observed_at"], selection_b["observed_at"])

                # The shared relative recency structure survives truncation:
                # the two newest tiers are kept, the oldest tier is dropped.
                self.assertEqual(
                    self._recency_tiers(selection_a["chunk_indices"]),
                    [2, 2, 2, 1, 1, 1],
                )

    # ── C. recency preserved ───────────────────────────────────────────────

    def test_recency_remains_primary_for_checksumless_items(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        for item in state.knowledge_layer.items:
            item.checksum_sha256 = ""

        # Give the newest timestamp to the item the tie-break ranks last, so
        # recency order is the exact reverse of fallback-digest order.
        by_identity = sorted(state.knowledge_layer.items, key=retrieval._semantic_identity)
        for offset, item in enumerate(by_identity):
            item.observed_at = (EVALUATED_AT - timedelta(hours=1 + offset)).isoformat()
            item.captured_at = item.observed_at

        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

        expected = [item.item_id for item in by_identity][:EXPECTED_MAX_ITEMS]
        self.assertEqual([record.item_id for record in view.eligible_items], expected)
        observed = [record.projection.observed_at for record in view.eligible_items]
        self.assertEqual(observed, sorted(observed, reverse=True))
        self.assertEqual(len(set(observed)), EXPECTED_MAX_ITEMS)


class TestRecencyOrdering(unittest.TestCase):
    """C. Genuinely different observed_at values still order newest-first."""

    @staticmethod
    def _synced_state(project_id: str = "retrieval-recency"):
        state = make_state(project_id)
        ensure_knowledge_layer(state)
        upsert_source_entry(
            state,
            SourceRegistryEntry(
                source_id="src-recency",
                name="Offline analyst fixture",
                source_kind="offline_fixture",
                connector_type="offline_fixture",
                owner="operator",
                access_mode="manual",
                sensitivity="internal",
                trust_tier="operator_curated",
            ),
        )
        sync_offline_source(
            state,
            "src-recency",
            [
                {
                    "source_ref": f"fixture://recency/{label}",
                    "title": f"{label} note",
                    "summary": f"Observation captured {hours}h before evaluation.",
                    "observed_at": (EVALUATED_AT - timedelta(hours=hours)).isoformat(),
                    "structured_payload": {"region": "mx", "score": 0.5},
                }
                for label, hours in (("older", 30), ("newest", 1), ("middle", 12))
            ],
            actor="operator",
            requested_at=EVALUATED_AT,
        )
        state.knowledge_layer.sources[0].last_success_at = (EVALUATED_AT - timedelta(hours=1)).isoformat()
        return state

    def test_newer_items_rank_before_older_items(self):
        state = self._synced_state()
        for phase in ("audit", "strategy"):
            with self.subTest(phase=phase):
                view = evaluate_phase_retrieval(state, phase, now=EVALUATED_AT)
                titles = [record.projection.title for record in view.eligible_items]
                observed = [record.projection.observed_at for record in view.eligible_items]
                self.assertEqual(titles, ["newest note", "middle note", "older note"])
                self.assertEqual(observed, sorted(observed, reverse=True))

    def test_recency_outranks_the_content_tie_break(self):
        state = self._synced_state("retrieval-recency-precedence")
        newest = next(item for item in state.knowledge_layer.items if item.title == "newest note")
        oldest = next(item for item in state.knowledge_layer.items if item.title == "older note")
        # Force the tie-break to favour the older item; recency must still win.
        newest.checksum_sha256 = "0" * 64
        oldest.checksum_sha256 = "f" * 64

        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

        self.assertEqual(view.eligible_items[0].projection.title, "newest note")
        self.assertEqual(view.eligible_items[-1].projection.title, "older note")

    def test_equal_observed_at_breaks_on_content_identity(self):
        state = self._synced_state("retrieval-recency-ties")
        shared_observed_at = (EVALUATED_AT - timedelta(hours=2)).isoformat()
        for item in state.knowledge_layer.items:
            item.observed_at = shared_observed_at

        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

        checksums = [
            next(
                item.checksum_sha256
                for item in state.knowledge_layer.items
                if item.item_id == record.item_id
            )
            for record in view.eligible_items
        ]
        self.assertEqual(checksums, sorted(checksums, reverse=True))


class TestTruncationLimits(unittest.TestCase):
    """D. Phase max_items behaviour is unchanged."""

    def test_prompt_facing_phase_limits_are_six(self):
        self.assertEqual(get_retrieval_policy("audit").prompt_exposure.max_items, EXPECTED_MAX_ITEMS)
        self.assertEqual(get_retrieval_policy("strategy").prompt_exposure.max_items, EXPECTED_MAX_ITEMS)

    def test_all_phase_limits_are_unchanged(self):
        expected = {
            "classify": 4,
            "hypotheses": 4,
            "gauntlet": 5,
            "audit": 6,
            "strategy": 6,
            "sqi": 4,
            "monitor": 8,
            "report": 8,
        }
        actual = {
            phase: get_retrieval_policy(phase).prompt_exposure.max_items
            for phase in retrieval.PHASE_SEQUENCE
        }
        self.assertEqual(actual, expected)

    def test_eligible_list_is_truncated_to_max_items(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        self.assertEqual(len(state.knowledge_layer.items), EXPECTED_ITEM_COUNT)
        for phase in ("audit", "strategy"):
            with self.subTest(phase=phase):
                view = evaluate_phase_retrieval(state, phase, now=EVALUATED_AT)
                self.assertEqual(len(view.eligible_items), EXPECTED_MAX_ITEMS)
                self.assertEqual(len(view.blocked_items), 0)

    def test_prompt_facing_phases_are_audit_and_strategy_only(self):
        self.assertEqual(PROMPT_FACING_RETRIEVAL_PHASES, ("audit", "strategy"))


class TestEligibilityGatesUnchanged(unittest.TestCase):
    """E. Trust / freshness / sensitivity / source / access gates are untouched."""

    def _state(self):
        return _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )

    def _blocked_reasons(self, state) -> set[str]:
        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)
        reasons: set[str] = set()
        for record in view.blocked_items:
            reasons.update(record.blocked_reasons)
        return reasons

    def test_source_level_gates_still_block(self):
        cases = {
            "source_disabled": ("enabled", False),
            "source_kind_disallowed": ("source_kind", "web_scrape"),
            "connector_type_disallowed": ("connector_type", "json"),
            "access_mode_disallowed": ("access_mode", "automated_api"),
        }
        for expected_reason, (field, value) in cases.items():
            with self.subTest(reason=expected_reason):
                state = self._state()
                setattr(state.knowledge_layer.sources[0], field, value)
                view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)
                self.assertEqual(len(view.eligible_items), 0)
                self.assertEqual(len(view.blocked_items), EXPECTED_ITEM_COUNT)
                self.assertIn(expected_reason, self._blocked_reasons(state))

    def test_item_level_gates_still_block(self):
        cases = {
            "sensitivity_disallowed": ("sensitivity", "restricted"),
            "trust_tier_below_minimum": ("trust_tier", "external_unknown"),
            "freshness_quarantined": ("freshness_status", KnowledgeItemStatus.QUARANTINED),
        }
        for expected_reason, (field, value) in cases.items():
            with self.subTest(reason=expected_reason):
                state = self._state()
                blocked_item = state.knowledge_layer.items[0]
                setattr(blocked_item, field, value)

                view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

                self.assertEqual(len(view.blocked_items), 1)
                self.assertIn(expected_reason, view.blocked_items[0].blocked_reasons)
                self.assertNotIn(
                    blocked_item.item_id,
                    [record.item_id for record in view.eligible_items],
                )

    def test_stale_and_expired_items_are_still_blocked(self):
        state = self._state()
        state.knowledge_layer.items[0].observed_at = (EVALUATED_AT - timedelta(hours=100)).isoformat()
        state.knowledge_layer.items[1].observed_at = (EVALUATED_AT - timedelta(hours=200)).isoformat()

        reasons = self._blocked_reasons(state)

        self.assertIn("freshness_stale", reasons)
        self.assertIn("freshness_expired", reasons)

    def test_manual_review_policy_still_blocks_everything(self):
        state = self._state()
        state.knowledge_layer.freshness_policies[0].manual_review_required = True

        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

        self.assertEqual(len(view.eligible_items), 0)
        self.assertIn("manual_review_required", self._blocked_reasons(state))


class TestRetrievalSurfaceCompatibility(unittest.TestCase):
    """F. Public retrieval views and retrieval-impact behaviour stay compatible."""

    def test_public_schemas_are_unchanged(self):
        self.assertEqual(
            set(RetrievalEligibleItem.model_fields),
            {
                "item_id",
                "source_id",
                "source_name",
                "title",
                "freshness_status",
                "trust_tier",
                "sensitivity",
                "source_status",
                "projection",
            },
        )
        self.assertEqual(
            set(ProjectedKnowledgeItem.model_fields),
            {
                "item_id",
                "source_id",
                "source_name",
                "title",
                "summary",
                "facts",
                "observed_at",
                "freshness_status",
                "trust_tier",
                "sensitivity",
                "untrusted_source",
                "prompt_exposure_note",
            },
        )
        self.assertEqual(
            set(PhaseKnowledgeRetrievalView.model_fields),
            {"project_id", "phase", "eligibility_source", "policy", "eligible_items", "blocked_items", "overview"},
        )

    def test_summary_and_impact_surfaces_still_work(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )

        summary = build_project_retrieval_summary(state, now=EVALUATED_AT)
        audit_impact = build_phase_retrieval_impact(state, "audit", now=EVALUATED_AT)
        prompt_facing = build_prompt_facing_retrieval_impact(state, now=EVALUATED_AT)

        self.assertEqual(len(summary.phases), len(retrieval.PHASE_SEQUENCE))
        self.assertEqual(summary.total_items, EXPECTED_ITEM_COUNT)
        audit_summary = next(phase for phase in summary.phases if phase.phase == "audit")
        self.assertEqual(audit_summary.eligible_count, EXPECTED_MAX_ITEMS)
        self.assertEqual(audit_summary.blocked_count, 0)

        self.assertEqual(audit_impact.eligible_count, EXPECTED_MAX_ITEMS)
        self.assertFalse(audit_impact.retrieval_used)
        self.assertEqual(audit_impact.used_item_count, 0)
        self.assertEqual([impact.phase for impact in prompt_facing], ["audit", "strategy"])

    def test_view_serializes_without_leaking_an_ordering_key(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

        payload = view.model_dump(mode="json")
        serialized = json.dumps(payload)

        self.assertEqual(len(payload["eligible_items"]), EXPECTED_MAX_ITEMS)
        self.assertNotIn("ordering_key", serialized)
        self.assertNotIn("checksum", serialized)
        for record in payload["eligible_items"]:
            self.assertIn("projection", record)
            self.assertTrue(record["projection"]["prompt_exposure_note"])


class TestRetrievalApiCompatibility(unittest.IsolatedAsyncioTestCase):
    """F. The API route returns the same deterministic ordering."""

    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_phase_route_matches_direct_evaluation(self):
        state_a = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        state_b = _uploaded_state(
            PROJECT_B, uploaded_at=UPLOADED_AT_B, random_material=UPLOAD_MATERIAL_B
        )

        # The route evaluates against the wall clock; freeze it so the dated
        # fixture uploads stay inside the freshness window.
        with patch("knowledge.retrieval.datetime", _frozen_datetime(EVALUATED_AT)):
            with patch("api.store.load", new=AsyncMock(return_value=state_a)):
                view_a = await api.get_knowledge_retrieval_phase(state_a.project_id, "audit")
            with patch("api.store.load", new=AsyncMock(return_value=state_b)):
                view_b = await api.get_knowledge_retrieval_phase(state_b.project_id, "audit")

        titles_a = [record.projection.title for record in view_a.eligible_items]
        titles_b = [record.projection.title for record in view_b.eligible_items]
        self.assertEqual(len(titles_a), EXPECTED_MAX_ITEMS)
        self.assertEqual(titles_a, titles_b)
        self.assertNotEqual(
            [record.item_id for record in view_a.eligible_items],
            [record.item_id for record in view_b.eligible_items],
        )


class TestFilenameBoundary(unittest.TestCase):
    """G. Documented boundary: prompt-facing titles embed the uploaded filename.

    Selection is filename-independent (chunk checksums hash bytes + index +
    text), but the projected *title text* is not. This patch deliberately does
    not redesign that: the D001 recreation must upload the exact same filename,
    in the exact same Unicode normalization form.
    """

    def test_projected_titles_embed_the_uploaded_filename(self):
        state = _uploaded_state(
            PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A
        )
        view = evaluate_phase_retrieval(state, "audit", now=EVALUATED_AT)

        self.assertTrue(view.eligible_items)
        for record in view.eligible_items:
            self.assertTrue(record.projection.title.startswith(f"{DOCUMENT_FILENAME} — "))
            self.assertEqual(record.projection.source_name, DOCUMENT_FILENAME)

    def test_a_different_filename_keeps_selection_but_changes_titles(self):
        baseline = _semantic_selection(
            _uploaded_state(PROJECT_A, uploaded_at=UPLOADED_AT_A, random_material=UPLOAD_MATERIAL_A),
            "audit",
        )
        renamed = _semantic_selection(
            _uploaded_state(
                PROJECT_B,
                uploaded_at=UPLOADED_AT_B,
                random_material=UPLOAD_MATERIAL_B,
                filename="CACOFONICO-renamed.docx",
            ),
            "audit",
        )

        self.assertEqual(baseline["chunk_indices"], renamed["chunk_indices"])
        self.assertEqual(baseline["checksums"], renamed["checksums"])
        # Documented boundary, not a defect fixed here:
        self.assertNotEqual(baseline["projection_rows"], renamed["projection_rows"])
        self.assertNotEqual(baseline["projection_digest"], renamed["projection_digest"])

    def test_unicode_normalization_of_the_filename_is_a_boundary(self):
        import unicodedata

        nfc_name = unicodedata.normalize("NFC", DOCUMENT_FILENAME)
        nfd_name = unicodedata.normalize("NFD", DOCUMENT_FILENAME)
        self.assertNotEqual(nfc_name, nfd_name)

        nfc = _semantic_selection(
            _uploaded_state(
                PROJECT_A,
                uploaded_at=UPLOADED_AT_A,
                random_material=UPLOAD_MATERIAL_A,
                filename=nfc_name,
            ),
            "audit",
        )
        nfd = _semantic_selection(
            _uploaded_state(
                PROJECT_B,
                uploaded_at=UPLOADED_AT_B,
                random_material=UPLOAD_MATERIAL_B,
                filename=nfd_name,
            ),
            "audit",
        )

        # Selection is identical; the prompt-facing title bytes are not.
        self.assertEqual(nfc["chunk_indices"], nfd["chunk_indices"])
        self.assertEqual(nfc["checksums"], nfd["checksums"])
        self.assertNotEqual(nfc["projection_digest"], nfd["projection_digest"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
