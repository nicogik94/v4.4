"""V4.4 pilot integrity P0-5 — the exact build that produced a run's evidence.

Observed defect: the machine archive carried ``code_version=4.4.0`` and
``git_sha=unknown``. The export manifest resolved the SHA at *export* time, so a
checkout-less container stamped nothing, and the SHA was never written to the
project state, so it could not survive the run that produced the evidence.

Deterministic and offline: no provider, no network, no database. The git lookup
is patched in every test that does not deliberately exercise the real one.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import exporters  # noqa: E402
import version  # noqa: E402
from state import ClassifyOutput, PhaseStatus, ProjectState  # noqa: E402


EXACT_SHA = "4a9cc77592b0b14a18f065a7c45920c9282fe528"
OTHER_SHA = "0eb246f9c0de4b1a9f5f7f7a1c2d3e4f5a6b7c8d"


def _state() -> ProjectState:
    state = ProjectState(project_id="pilot-integrity-build", project_name="Build", brief="Decide.")
    state.classify = ClassifyOutput(domain="Complicated", justification="j", bf=42.0, dq=[10, 10, 10, 10])
    state.report = "# Executive Summary\nRun the bounded pilot.\n"
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
    return state


class TestResolution(unittest.TestCase):
    def test_normal_checkout_yields_an_exact_sha(self):
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            provenance = version.current_build_provenance({})
        self.assertEqual(provenance.git_sha, EXACT_SHA)
        self.assertEqual(provenance.git_sha_status, version.GIT_SHA_EXACT)
        self.assertEqual(provenance.git_sha_source, version.SOURCE_GIT_REV_PARSE)
        self.assertTrue(provenance.is_exact)

    def test_github_sha_environment_is_preferred(self):
        with patch.object(version, "_git_head_sha", return_value=OTHER_SHA):
            provenance = version.current_build_provenance({"GITHUB_SHA": EXACT_SHA})
        self.assertEqual(provenance.git_sha, EXACT_SHA)
        self.assertEqual(provenance.git_sha_source, version.SOURCE_GITHUB_SHA)

    def test_unavailable_is_explicit_and_never_the_string_unknown(self):
        with patch.object(version, "_git_head_sha", return_value=""):
            provenance = version.current_build_provenance({})
        self.assertEqual(provenance.git_sha, "")
        self.assertEqual(provenance.git_sha_status, version.GIT_SHA_UNAVAILABLE)
        self.assertFalse(provenance.is_exact)

    def test_a_short_sha_or_ref_name_is_never_accepted(self):
        for candidate in ("4a9cc77", "HEAD", "main", "not-a-sha", "", "4A9CC77592B0B14A18F065A7C45920C9282FE52"):
            with self.subTest(candidate=candidate):
                self.assertEqual(version.normalize_exact_sha(candidate), "")

    def test_an_uppercase_exact_sha_is_normalized_not_rejected(self):
        self.assertEqual(version.normalize_exact_sha(EXACT_SHA.upper()), EXACT_SHA)

    def test_explicit_build_metadata_env_override_is_exact(self):
        """Explicit runtime/build metadata resolves without any git metadata.

        Replaces an earlier test that asserted the *test runtime itself* was a
        git checkout. That coupled the provenance contract to where the suite
        happened to run: the app container has no ``/app/.git`` and no ``git``
        binary, where ``unavailable`` is the correct answer, not a defect. The
        contract under test is the resolution order, so every branch of it is
        driven explicitly here and in the three tests around it.
        """
        for name in ("V4_GIT_SHA", "GIT_SHA"):
            with self.subTest(env=name):
                # Git deliberately reports a *different* commit, so this also
                # proves the override wins rather than merely agreeing.
                with patch.object(version, "_git_head_sha", return_value=OTHER_SHA):
                    provenance = version.current_build_provenance({name: EXACT_SHA})
                self.assertEqual(provenance.git_sha, EXACT_SHA)
                self.assertEqual(provenance.git_sha_status, version.GIT_SHA_EXACT)
                self.assertEqual(provenance.git_sha_source, version.SOURCE_ENV_OVERRIDE)
                self.assertTrue(provenance.is_exact)

    def test_an_invalid_env_sha_never_resolves_exact(self):
        """A short SHA, a ref name or garbage must not become an exact build."""
        for name in ("GITHUB_SHA", "V4_GIT_SHA", "GIT_SHA"):
            for candidate in ("4a9cc77", "HEAD", "main", "not-a-sha", EXACT_SHA[:-1]):
                with self.subTest(env=name, candidate=candidate):
                    with patch.object(version, "_git_head_sha", return_value=""):
                        provenance = version.current_build_provenance({name: candidate})
                    self.assertEqual(provenance.git_sha, "")
                    self.assertEqual(provenance.git_sha_status, version.GIT_SHA_UNAVAILABLE)
                    self.assertFalse(provenance.is_exact)

    def test_an_invalid_env_sha_falls_through_to_git_rather_than_winning(self):
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            provenance = version.current_build_provenance({"GITHUB_SHA": "main"})
        self.assertEqual(provenance.git_sha, EXACT_SHA)
        self.assertEqual(provenance.git_sha_source, version.SOURCE_GIT_REV_PARSE)

    def test_short_display_sha_helper_is_unchanged(self):
        """/health and /runtime/preflight keep their existing behaviour."""
        self.assertTrue(version.get_git_sha())


class TestRecordingOnProjectState(unittest.TestCase):
    def test_recording_stamps_the_run(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
        record = state.build_provenance
        self.assertEqual(record["git_sha"], EXACT_SHA)
        self.assertEqual(record["git_sha_status"], version.GIT_SHA_EXACT)
        self.assertTrue(record["code_version"])
        self.assertTrue(record["recorded_at"])

    def test_a_recorded_sha_is_never_overwritten_by_a_later_environment(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
        with patch.object(version, "_git_head_sha", return_value=OTHER_SHA):
            version.record_build_provenance(state, {})
        self.assertEqual(state.build_provenance["git_sha"], EXACT_SHA)

    def test_an_unavailable_record_is_not_upgraded_later(self):
        """A run whose build could not be identified stays unidentified."""
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=""):
            version.record_build_provenance(state, {})
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
        self.assertEqual(state.build_provenance["git_sha"], "")
        self.assertEqual(state.build_provenance["git_sha_status"], version.GIT_SHA_UNAVAILABLE)

    def test_state_provenance_prefers_the_recorded_run_over_the_environment(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
        with patch.object(version, "_git_head_sha", return_value=OTHER_SHA):
            provenance = version.state_build_provenance(state, {})
        self.assertEqual(provenance.git_sha, EXACT_SHA)
        self.assertEqual(provenance.origin, version.ORIGIN_RECORDED_RUN)

    def test_state_provenance_falls_back_to_the_ambient_build(self):
        state = _state()  # persisted before this fix: nothing recorded
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            provenance = version.state_build_provenance(state, {})
        self.assertEqual(provenance.git_sha, EXACT_SHA)
        self.assertEqual(provenance.origin, version.ORIGIN_AMBIENT)


class TestPropagationIntoExportEvidence(unittest.TestCase):
    def test_manifest_carries_the_recorded_sha_from_a_git_less_environment(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
        with patch.object(version, "_git_head_sha", return_value=""):
            manifest = exporters.build_export_manifest(state, "machine_archive", "zip")
        self.assertEqual(manifest["git_sha"], EXACT_SHA)
        self.assertEqual(manifest["git_sha_status"], version.GIT_SHA_EXACT)
        self.assertEqual(manifest["git_sha_origin"], version.ORIGIN_RECORDED_RUN)
        self.assertTrue(manifest["exact_build_provenance"])

    def test_archive_records_unavailable_explicitly(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=""):
            version.record_build_provenance(state, {})
            manifest = exporters.build_machine_archive_payload(state)["export_manifest.json"]
        self.assertEqual(manifest["git_sha"], "")
        self.assertEqual(manifest["git_sha_status"], version.GIT_SHA_UNAVAILABLE)
        self.assertFalse(manifest["exact_build_provenance"])

    def test_archive_carries_the_sha_end_to_end(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
            files = exporters.build_machine_archive_payload(state)
        self.assertEqual(files["export_manifest.json"]["git_sha"], EXACT_SHA)
        self.assertEqual(files["project_state.json"]["build_provenance"]["git_sha"], EXACT_SHA)

    def test_export_does_not_stamp_the_state(self):
        state = _state()
        before = state.model_dump(mode="json")
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            exporters.build_machine_archive_payload(state)
        self.assertEqual(state.model_dump(mode="json"), before)


class TestCertificationPath(unittest.TestCase):
    def test_certification_fails_without_an_exact_sha(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=""):
            version.record_build_provenance(state, {})
            with self.assertRaises(version.ExactBuildProvenanceError):
                version.require_exact_build_provenance(state, {})

    def test_certification_passes_with_an_exact_sha(self):
        state = _state()
        with patch.object(version, "_git_head_sha", return_value=EXACT_SHA):
            version.record_build_provenance(state, {})
            provenance = version.require_exact_build_provenance(state, {})
        self.assertEqual(provenance.git_sha, EXACT_SHA)


if __name__ == "__main__":
    unittest.main()
